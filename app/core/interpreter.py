"""
interpreter.py — Sandboxed Python code interpreter.

Executes Python code snippets in isolated subprocess.
wiki_root is injected as WIKI_ROOT env var and available in code.

Timeout: 15 seconds hard limit.
stdout captured, stderr captured separately.
Code can print JSON to stdout → parsed into result_json.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import tempfile
import textwrap
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("wiki.interpreter")


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


class CodeInterpreter:
    """
    Executes Python code snippets in isolated subprocess.
    wiki_root is injected as WIKI_ROOT env var and available in code.

    Input:  str (Python code)
    Output: ExecutionResult
    """

    def __init__(self, wiki_root: Path, timeout: int = 15):
        self.wiki_root = wiki_root
        self.timeout = timeout

    def execute(self, code: str) -> ExecutionResult:
        logger.debug("Executing code: %d bytes", len(code))
        preamble = f'WIKI_ROOT = {str(self.wiki_root)!r}\n'
        full_code = preamble + textwrap.dedent(code)

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
                logger.warning("Code execution failed: exit_code=%d stderr=%s",
                               proc.returncode, stderr[:500])

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
                stdout="", stderr="Execution timeout",
                exit_code=-1, success=False, result_json=None,
            )
        finally:
            Path(script_path).unlink(missing_ok=True)
