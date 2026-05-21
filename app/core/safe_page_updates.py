"""
safe_page_updates.py — Typed page write operations with diff generation.

Instead of arbitrary LLM patch (which can lose frontmatter or unrelated sections),
all page updates go through structured operations:

  - replace_section: replace content under a heading
  - append_section: add new content after a heading
  - update_frontmatter_field: change a single frontmatter field
  - add_provenance_marker: add ^[raw/...] marker after a quote

Each operation produces a deterministic diff before applying.
Large updates (>review_threshold chars) require human review.
"""

from __future__ import annotations

import difflib
import logging
import re
from dataclasses import dataclass, field
from datetime import date

import frontmatter

from app.core.wiki_fs import WikiPage

logger = logging.getLogger("wiki.safe_updates")


# ─────────────────────────────────────────────
# Typed operations
# ─────────────────────────────────────────────

@dataclass
class ReplaceSection:
    """Replace all content under a heading with new content."""
    heading: str              # exact heading text to match
    new_content: str
    preserve_provenance: bool = True   # keep existing ^[...] markers


@dataclass
class AppendSection:
    """Append new content after a heading (creates section if missing)."""
    heading: str
    content: str
    as_subsection: bool = False        # create as ## under # heading


@dataclass
class UpdateFrontmatterField:
    """Update a single frontmatter field."""
    field_name: str
    field_value: str | int | float | list | None


@dataclass
class AddProvenanceMarker:
    """Add a ^[raw/...] provenance marker after a text match."""
    after_text: str           # text to find (first occurrence)
    source_ref: str           # e.g. "raw/myapp/deploy_guide.md"


PageOperation = ReplaceSection | AppendSection | UpdateFrontmatterField | AddProvenanceMarker


@dataclass
class PageWritePlan:
    """Plan for updating a wiki page with typed operations."""
    slug: str
    operations: list[PageOperation] = field(default_factory=list)
    reason: str = ""           # why this update is needed

    @property
    def is_empty(self) -> bool:
        return len(self.operations) == 0

    @property
    def char_delta_estimate(self) -> int:
        """Rough estimate of character change."""
        delta = 0
        for op in self.operations:
            if isinstance(op, ReplaceSection):
                delta -= 500  # rough average section size
                delta += len(op.new_content)
            elif isinstance(op, AppendSection):
                delta += len(op.content)
            elif isinstance(op, UpdateFrontmatterField):
                delta += len(str(op.field_value or ""))
            elif isinstance(op, AddProvenanceMarker):
                delta += len(op.source_ref) + 4  # ^[...]
        return delta


@dataclass
class PageUpdateDiff:
    """Diff result for a page update plan."""
    slug: str
    old_content: str
    new_content: str
    diff_lines: list[str]
    char_delta: int
    frontmatter_preserved: bool
    requires_review: bool
    review_reason: str = ""


# ─────────────────────────────────────────────
# Apply operations
# ─────────────────────────────────────────────

def apply_operations(
    page: WikiPage,
    plan: PageWritePlan,
) -> tuple[dict, str]:
    """
    Apply a list of typed operations to a wiki page.

    Returns (meta, content) tuple ready for WikiFS.write_page().
    Preserves frontmatter and unrelated sections.

    Raises ValueError if an operation cannot be applied.
    """
    meta = dict(page.meta)
    content = page.content

    for op in plan.operations:
        if isinstance(op, ReplaceSection):
            content = _replace_section(content, op)
        elif isinstance(op, AppendSection):
            content = _append_section(content, op)
        elif isinstance(op, UpdateFrontmatterField):
            meta = _update_frontmatter(meta, op)
        elif isinstance(op, AddProvenanceMarker):
            content = _add_provenance(content, op)

    # Ensure required fields
    meta.setdefault("last_confirmed", date.today().isoformat())
    meta.setdefault("created", page.meta.get("created", date.today().isoformat()))

    return meta, content


def _replace_section(content: str, op: ReplaceSection) -> str:
    """Replace content under a heading."""
    heading_re = re.compile(
        rf"^(#{{1,6}})\s+{re.escape(op.heading)}\s*$",
        re.MULTILINE,
    )
    match = heading_re.search(content)
    if not match:
        raise ValueError(f"Heading not found: '{op.heading}'")

    heading_level = len(match.group(1))
    start = match.end()

    # Find end: next heading of same or higher level
    next_heading_re = re.compile(
        rf"^(#{{1,{heading_level}}})\s+",
        re.MULTILINE,
    )
    next_match = next_heading_re.search(content, start)

    if next_match:
        old_section = content[start:next_match.start()]
        new_content = content[:start] + "\n" + op.new_content + "\n\n" + content[next_match.start():]
    else:
        old_section = content[start:]
        new_content = content[:start] + "\n" + op.new_content + "\n"

    # Preserve provenance markers if requested
    if op.preserve_provenance:
        existing_markers = re.findall(r"\^\[([^\]]+)\]", old_section)
        for marker in existing_markers:
            if f"^[{marker}]" not in new_content:
                # Append preserved markers at end of new section
                new_content = new_content.rstrip() + f"\n\n^[{marker}]\n"

    return new_content


def _append_section(content: str, op: AppendSection) -> str:
    """Append content after a heading."""
    heading_re = re.compile(
        rf"^(#{{1,6}})\s+{re.escape(op.heading)}\s*$",
        re.MULTILINE,
    )
    match = heading_re.search(content)

    if not match:
        # Heading not found — create it
        if op.as_subsection:
            # Find the last top-level heading and add as subsection
            headings = list(re.finditer(r"^(#{1,6})\s+(.+)$", content, re.MULTILINE))
            if headings:
                last = headings[-1]
                insert_pos = last.end()
                # Find end of last section
                next_h = re.search(r"^(#{1,6})\s+", content[insert_pos:], re.MULTILINE)
                if next_h:
                    insert_pos = insert_pos + next_h.start()
                new_heading = f"\n## {op.heading}\n\n{op.content}\n"
                return content[:insert_pos] + new_heading + content[insert_pos:]
            else:
                return content + f"\n## {op.heading}\n\n{op.content}\n"
        else:
            # Append at end
            return content.rstrip() + f"\n\n## {op.heading}\n\n{op.content}\n"

    # Heading exists — append after it
    start = match.end()
    next_heading_re = re.compile(r"^(#{1,6})\s+", re.MULTILINE)
    next_match = next_heading_re.search(content, start)

    if next_match:
        return content[:next_match.start()] + "\n" + op.content + "\n\n" + content[next_match.start():]
    else:
        return content.rstrip() + "\n\n" + op.content + "\n"


def _update_frontmatter(
    meta: dict,
    op: UpdateFrontmatterField,
) -> dict:
    """Update a single frontmatter field."""
    if op.field_value is None:
        meta.pop(op.field_name, None)
    else:
        meta[op.field_name] = op.field_value
    return meta


def _add_provenance(content: str, op: AddProvenanceMarker) -> str:
    """Add provenance marker after first occurrence of text."""
    idx = content.find(op.after_text)
    if idx == -1:
        # Text not found — append at end
        logger.warning(
            "Provenance marker text not found: %s (source: %s)",
            op.after_text[:50], op.source_ref,
        )
        return content.rstrip() + f"\n\n^[{op.source_ref}]\n"

    # Insert after the text
    insert_pos = idx + len(op.after_text)
    marker = f" ^[{op.source_ref}]"
    return content[:insert_pos] + marker + content[insert_pos:]


# ─────────────────────────────────────────────
# Diff generation
# ─────────────────────────────────────────────

def generate_diff(
    page: WikiPage,
    plan: PageWritePlan,
    review_threshold_chars: int = 2000,
) -> PageUpdateDiff:
    """
    Generate a deterministic diff for a page update plan.

    Does NOT apply the changes — only shows what would change.
    Returns PageUpdateDiff with old/new content and diff lines.
    """
    if plan.is_empty:
        return PageUpdateDiff(
            slug=plan.slug,
            old_content=page.raw,
            new_content=page.raw,
            diff_lines=[],
            char_delta=0,
            frontmatter_preserved=True,
            requires_review=False,
        )

    # Apply operations to get new content
    try:
        new_meta, new_content = apply_operations(page, plan)
    except ValueError as exc:
        raise ValueError(f"Cannot generate diff: {exc}") from exc

    # Build full new raw content
    new_post = frontmatter.Post(new_content, **new_meta)
    new_raw = frontmatter.dumps(new_post)

    # Generate unified diff
    diff = list(difflib.unified_diff(
        page.raw.splitlines(keepends=True),
        new_raw.splitlines(keepends=True),
        fromfile=f"wiki/{page.slug}",
        tofile=f"updated/{page.slug}",
    ))

    # Check frontmatter preservation
    old_has_fm = page.raw.startswith("---")
    new_has_fm = new_raw.startswith("---")
    fm_preserved = old_has_fm and new_has_fm

    # Check if review is needed
    char_delta = abs(len(new_raw) - len(page.raw))
    needs_review = False
    review_reason = ""

    if char_delta > review_threshold_chars:
        needs_review = True
        review_reason = f"Change exceeds {review_threshold_chars} chars (delta: {char_delta})"
    elif not fm_preserved:
        needs_review = True
        review_reason = "Frontmatter may not be preserved"
    elif len(plan.operations) > 5:
        needs_review = True
        review_reason = f"Large number of operations ({len(plan.operations)})"

    return PageUpdateDiff(
        slug=plan.slug,
        old_content=page.raw,
        new_content=new_raw,
        diff_lines=diff,
        char_delta=char_delta,
        frontmatter_preserved=fm_preserved,
        requires_review=needs_review,
        review_reason=review_reason,
    )


# ─────────────────────────────────────────────
# Validation
# ─────────────────────────────────────────────

def validate_operation(op: PageOperation) -> list[str]:
    """
    Validate a single operation before applying.
    Returns list of error messages (empty if valid).
    """
    errors = []

    if isinstance(op, ReplaceSection):
        if not op.heading.strip():
            errors.append("ReplaceSection: heading cannot be empty")
        if not op.new_content.strip():
            errors.append("ReplaceSection: new_content cannot be empty")

    elif isinstance(op, AppendSection):
        if not op.heading.strip():
            errors.append("AppendSection: heading cannot be empty")
        if not op.content.strip():
            errors.append("AppendSection: content cannot be empty")

    elif isinstance(op, UpdateFrontmatterField):
        if not op.field_name.strip():
            errors.append("UpdateFrontmatterField: field_name cannot be empty")

    elif isinstance(op, AddProvenanceMarker):
        if not op.source_ref.startswith("raw/"):
            errors.append(f"AddProvenanceMarker: source_ref must start with 'raw/', got: {op.source_ref}")

    return errors


def validate_plan(plan: PageWritePlan) -> list[str]:
    """Validate all operations in a plan."""
    errors = []
    for op in plan.operations:
        errors.extend(validate_operation(op))
    return errors
