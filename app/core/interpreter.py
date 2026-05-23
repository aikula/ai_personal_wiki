"""
interpreter.py — Sandboxed Python code interpreter.

Executes Python code snippets in isolated subprocess.
wiki_root is injected as WIKI_ROOT env var and available in code.

Sandbox layers:
1. AST-based static analysis blocks forbidden imports and calls
2. Resource limits (CPU, memory, file size) via setrlimit (POSIX only)
3. Subprocess timeout: 10 seconds hard limit
"""

from __future__ import annotations

import ast
import json
import logging
import platform
import subprocess
import sys
import tempfile
import textwrap
from dataclasses import dataclass
from pathlib import Path

try:
    import resource

    _RESOURCE_AVAILABLE = True
except ImportError:
    _RESOURCE_AVAILABLE = False

logger = logging.getLogger("wiki.interpreter")

# ── Allowed imports ──────────────────────────────────────────────
_ALLOWED_STDLIB = {
    "re",
    "json",
    "pathlib",
    "datetime",
    "time",
    "collections",
    "itertools",
    "difflib",
    "functools",
    "textwrap",
    "math",
    "random",
    "statistics",
    "decimal",
    "fractions",
    "hashlib",
    "base64",
    "string",
    "typing",
    "types",
    "inspect",
    "pprint",
    "uuid",
    "copy",
    "enum",
    "numbers",
    "abc",
    "dataclasses",
    "zoneinfo",
    "builtins",
}

_ALLOWED_THIRD_PARTY = {"rapidfuzz", "frontmatter", "yaml"}

_ALLOWED_IMPORTS = _ALLOWED_STDLIB | _ALLOWED_THIRD_PARTY

# ── Forbidden calls ──────────────────────────────────────────────
# (module_or_name, attribute_or_none) → both match "os.system" and bare "eval"
_FORBIDDEN_CALLS = {
    ("eval", None),
    ("exec", None),
    ("compile", None),
    ("__import__", None),
    ("os", "system"),
    ("os", "popen"),
    ("os", "spawnl"),
    ("os", "spawnle"),
    ("os", "spawnlp"),
    ("os", "spawnlpe"),
    ("os", "spawnv"),
    ("os", "spawnve"),
    ("os", "spawnvp"),
    ("os", "spawnvpe"),
    ("subprocess", "run"),
    ("subprocess", "call"),
    ("subprocess", "check_call"),
    ("subprocess", "check_output"),
    ("subprocess", "Popen"),
    ("socket", None),
    ("requests", None),
    ("httpx", None),
    ("urllib", None),
    ("urllib.request", None),
    ("urllib.parse", None),
    ("ftplib", None),
    ("smtplib", None),
    ("telnetlib", None),
    ("xmlrpc", None),
    ("xmlrpc.client", None),
    ("xmlrpc.server", None),
    ("webbrowser", None),
}

# ── Resource limits (POSIX) ────────────────────────────────────
_MAX_MEMORY_BYTES = 256 * 1024 * 1024  # 256 MB
_MAX_FILE_SIZE_BYTES = 1 * 1024 * 1024  # 1 MB


# ── Data model ───────────────────────────────────────────────────


@dataclass
class ExecutionResult:
    stdout: str
    stderr: str
    exit_code: int
    success: bool
    result_json: dict | None

    def to_dict(self) -> dict:
        return {
            "stdout": self.stdout[:2000],
            "stderr": self.stderr[:500],
            "success": self.success,
            "result": self.result_json,
        }


# ── AST Sandbox checker ──────────────────────────────────────────


class _SandboxChecker(ast.NodeVisitor):
    """AST visitor that rejects forbidden imports and calls."""

    def __init__(self) -> None:
        self.errors: list[str] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._check_module(alias.name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        self._check_module(module)
        self.generic_visit(node)

    def _check_module(self, module: str) -> None:
        top = module.split(".")[0]
        if top not in _ALLOWED_IMPORTS:
            self.errors.append(f"Forbidden import: {module}")

    def visit_Call(self, node: ast.Call) -> None:
        name, attr = self._resolve_call(node.func)
        if (name, attr) in _FORBIDDEN_CALLS or (name, None) in _FORBIDDEN_CALLS:
            target = f"{name}.{attr}" if attr else name
            self.errors.append(f"Forbidden call: {target}()")
        elif name == "open":
            self._check_open_mode(node)
        self.generic_visit(node)

    def _resolve_call(self, func) -> tuple[str, str | None]:
        if isinstance(func, ast.Name):
            return func.id, None
        if isinstance(func, ast.Attribute):
            parts = []
            node = func
            while isinstance(node, ast.Attribute):
                parts.append(node.attr)
                node = node.value
            if isinstance(node, ast.Name):
                parts.append(node.id)
                parts.reverse()
                return parts[0], parts[1] if len(parts) > 1 else None
        return "", None

    def _check_open_mode(self, node: ast.Call) -> None:
        mode = "r"
        if len(node.args) >= 2:
            arg = node.args[1]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                mode = arg.value
        for kw in node.keywords:
            if kw.arg == "mode":
                if isinstance(kw.value, ast.Constant) and isinstance(
                    kw.value.value, str
                ):
                    mode = kw.value.value
                break
        if any(c in mode for c in "wax+"):
            self.errors.append(f"Forbidden open() mode: {mode}")


# ── Interpreter ──────────────────────────────────────────────────


class CodeInterpreter:
    """
    Executes Python code snippets in isolated subprocess.
    wiki_root is injected as WIKI_ROOT env var and available in code.

    Input:  str (Python code)
    Output: ExecutionResult
    """

    def __init__(self, wiki_root: Path, timeout: int = 10):
        self.wiki_root = wiki_root
        self.timeout = timeout

    def execute(self, code: str) -> ExecutionResult:
        logger.debug("Executing code: %d bytes", len(code))
        preamble = f"WIKI_ROOT = {str(self.wiki_root)!r}\n"
        full_code = preamble + textwrap.dedent(code)

        # 1. Static analysis (AST sandbox)
        try:
            tree = ast.parse(full_code)
        except SyntaxError as exc:
            return ExecutionResult(
                stdout="",
                stderr=f"Syntax error: {exc}",
                exit_code=-1,
                success=False,
                result_json=None,
            )

        checker = _SandboxChecker()
        checker.visit(tree)
        if checker.errors:
            return ExecutionResult(
                stdout="",
                stderr="Sandbox violation: " + "; ".join(checker.errors),
                exit_code=-1,
                success=False,
                result_json=None,
            )

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8"
        ) as f:
            f.write(full_code)
            script_path = f.name

        try:
            proc = subprocess.run(
                [sys.executable, script_path],
                capture_output=True,
                text=True,
                timeout=self.timeout,
                preexec_fn=self._set_limits
                if _RESOURCE_AVAILABLE and platform.system() != "Windows"
                else None,
            )
            stdout = proc.stdout.strip()
            stderr = proc.stderr.strip()
            success = proc.returncode == 0

            result_json = None
            if stdout:
                try:
                    result_json = json.loads(stdout)
                except json.JSONDecodeError:
                    pass

            if not success:
                logger.warning(
                    "Code execution failed: exit_code=%d stderr=%s",
                    proc.returncode,
                    stderr[:500],
                )

            return ExecutionResult(
                stdout=stdout,
                stderr=stderr,
                exit_code=proc.returncode,
                success=success,
                result_json=result_json,
            )
        except subprocess.TimeoutExpired:
            logger.warning("Code execution timed out: timeout=%ds", self.timeout)
            return ExecutionResult(
                stdout="",
                stderr="Execution timeout",
                exit_code=-1,
                success=False,
                result_json=None,
            )
        finally:
            Path(script_path).unlink(missing_ok=True)

    def _set_limits(self) -> None:
        """Apply resource limits in child process (POSIX only)."""
        if not _RESOURCE_AVAILABLE:
            return
        try:
            resource.setrlimit(
                resource.RLIMIT_CPU,
                (self.timeout, self.timeout + 1),
            )
            resource.setrlimit(
                resource.RLIMIT_AS,
                (_MAX_MEMORY_BYTES, _MAX_MEMORY_BYTES),
            )
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
            resource.setrlimit(
                resource.RLIMIT_FSIZE,
                (_MAX_FILE_SIZE_BYTES, _MAX_FILE_SIZE_BYTES),
            )
        except (ValueError, OSError) as exc:
            logger.warning("Failed to set resource limits: %s", exc)
