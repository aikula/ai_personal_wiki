"""
linter_models.py — Result models for the structural wiki linter.
"""

from __future__ import annotations

from dataclasses import dataclass, field

ISSUE_KINDS = {
    "broken_wikilink",
    "broken_path_link",
    "missing_anchor",
    "orphan_page",
    "missing_frontmatter",
    "char_limit",
    "superseded_active",
    "stale_page",
    "duplicate_title",
    "missing_wikilink",
    "invalid_provenance",
    "source_drift",
    "missing_source",
    "orphan_source_card",
    "orphan_claim",
    "claim_without_source_card",
    "contradicted_claim_still_active",
}


@dataclass
class LintIssue:
    slug: str            # affected page slug
    line: int            # 0 if not line-specific
    kind: str            # one of ISSUE_KINDS
    detail: str          # human-readable description
    severity: str        # "error" | "warning" | "info"
    fix_hint: str = ""   # what agent should do to fix

    def __str__(self) -> str:
        loc = f"{self.slug}:{self.line}" if self.line else self.slug
        return f"[{self.severity.upper()}] {loc} — {self.detail}"


@dataclass
class LintReport:
    ran_at: str
    total_pages: int
    issues: list[LintIssue] = field(default_factory=list)

    @property
    def errors(self) -> list[LintIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[LintIssue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def by_kind(self) -> dict[str, list[LintIssue]]:
        result: dict[str, list[LintIssue]] = {}
        for issue in self.issues:
            result.setdefault(issue.kind, []).append(issue)
        return result

    @property
    def is_clean(self) -> bool:
        return len(self.errors) == 0

    def summary(self) -> str:
        if self.is_clean:
            return f"✓ Wiki проверена. Страниц: {self.total_pages}."
        return (
            f"✗ {len(self.errors)} ошибок, {len(self.warnings)} предупреждений "
            f"в {self.total_pages} страницах."
        )
