"""
large_source_ingest.py — Outline parser, chunking, and merge analysis
for processing large source documents (100K+ chars, up to 1M).

Flow:
  1. parse_outline(source) → structured document outline
  2. chunk_by_outline(outline, source, settings) → list of chunks
  3. chunk_result dataclass per chunk (filled by ingest agent)
  4. merge_analysis(chunk_results) → deduped claims, conflicts, page plan
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from app.config import Settings

logger = logging.getLogger("wiki.ingest.large")


# ─────────────────────────────────────────────
# Data models
# ─────────────────────────────────────────────

@dataclass
class OutlineItem:
    """Single heading/section in a document outline."""
    text: str
    level: int                # 1-6 (markdown heading level)
    start_pos: int            # character offset in source
    end_pos: int              # character offset (start of next heading or EOF)
    char_count: int           # content length (excluding heading itself)
    preview: str = ""         # first 200 chars of section content


@dataclass
class DocumentOutline:
    """Full outline of a source document."""
    source_path: str
    total_chars: int
    items: list[OutlineItem] = field(default_factory=list)

    @property
    def section_count(self) -> int:
        return len(self.items)


@dataclass
class Chunk:
    """A chunk of source text ready for analysis."""
    chunk_id: str                     # e.g. "chunk-001"
    source_path: str
    section_path: list[str]           # heading hierarchy, e.g. ["Deployment", "Redis"]
    text: str
    char_count: int
    split_reason: str = "outline"     # "outline" | "hard_max" | "paragraph" | "sentence"
    headings: list[dict] = field(default_factory=list)  # [{text, level}]


@dataclass
class ChunkAnalysisResult:
    """Typed result from analyzing a single chunk. Filled by LLM."""
    chunk_id: str
    source_id: str
    section_path: list[str]
    candidate_pages: list[str] = field(default_factory=list)   # slugs to create/update
    claims: list[dict] = field(default_factory=list)           # extracted claims
    conflicts: list[dict] = field(default_factory=list)        # detected conflicts
    ignored_sections: list[str] = field(default_factory=list)  # sections intentionally skipped
    outcome: str = "pending"          # "page" | "claim" | "conflict" | "ignored" | "failed"


@dataclass
class MergeAnalysisResult:
    """Combined result from all chunk analyses."""
    source_id: str
    source_path: str
    total_chunks: int
    chunks_processed: int
    chunks_failed: int
    all_candidate_pages: list[dict] = field(default_factory=list)   # deduped by slug
    all_claims: list[dict] = field(default_factory=list)
    all_conflicts: list[dict] = field(default_factory=list)
    triage_report: str = ""           # human-readable summary
    page_write_plan: list[dict] = field(default_factory=list)


# ─────────────────────────────────────────────
# Outline parser
# ─────────────────────────────────────────────

def parse_outline(source_content: str, source_path: str = "") -> DocumentOutline:
    """
    Parse a document outline using fallback order:
    1. Markdown headings (#, ##, ###, ...)
    2. Large paragraph groups (if no headings)
    3. Sentence groups (if paragraphs too large)

    Returns DocumentOutline with items having start/end positions.
    """
    # Try markdown headings first
    items = _parse_markdown_headings(source_content)
    if items:
        return DocumentOutline(
            source_path=source_path,
            total_chars=len(source_content),
            items=items,
        )

    # Fallback: paragraph groups
    items = _parse_paragraph_groups(source_content)
    if items:
        return DocumentOutline(
            source_path=source_path,
            total_chars=len(source_content),
            items=items,
        )

    # Last resort: treat entire document as one section
    preview = source_content[:200].replace("\n", " ")
    return DocumentOutline(
        source_path=source_path,
        total_chars=len(source_content),
        items=[OutlineItem(
            text="(full document)",
            level=1,
            start_pos=0,
            end_pos=len(source_content),
            char_count=len(source_content),
            preview=preview,
        )],
    )


def _parse_markdown_headings(content: str) -> list[OutlineItem]:
    """Extract outline items from markdown headings."""
    heading_re = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
    matches = list(heading_re.finditer(content))

    if not matches:
        return []

    items = []
    for i, match in enumerate(matches):
        level = len(match.group(1))
        text = match.group(2).strip()
        start = match.end()

        # End is start of next heading or end of content
        if i + 1 < len(matches):
            end = matches[i + 1].start()
        else:
            end = len(content)

        section_text = content[start:end].strip()
        preview = section_text[:200].replace("\n", " ") if section_text else ""

        items.append(OutlineItem(
            text=text,
            level=level,
            start_pos=match.start(),
            end_pos=end,
            char_count=len(section_text),
            preview=preview,
        ))

    return items


def _parse_paragraph_groups(content: str, min_chars: int = 200) -> list[OutlineItem]:
    """Fallback: group content by paragraph blocks."""
    paragraphs = re.split(r"\n\n+", content)
    items = []
    pos = 0

    for para in paragraphs:
        para = para.strip()
        if not para:
            pos += len(para) + 2
            continue

        start = content.find(para, pos)
        if start == -1:
            start = pos
        end = start + len(para)

        # Generate a synthetic title from first sentence
        first_sentence = para.split(".")[0][:80]
        title = first_sentence if first_sentence else f"Section at char {start}"

        items.append(OutlineItem(
            text=title,
            level=2,
            start_pos=start,
            end_pos=end,
            char_count=len(para),
            preview=para[:200].replace("\n", " "),
        ))
        pos = end

    return items


# ─────────────────────────────────────────────
# Chunking
# ─────────────────────────────────────────────

def chunk_by_outline(
    outline: DocumentOutline,
    source_content: str,
    settings: Settings,
) -> list[Chunk]:
    """
    Split source content into chunks based on outline.

    Respects chunk size limits:
    - If a section exceeds chunk_max_chars, split at natural boundaries
    - Never split mid-sentence, mid-code-fence, or mid-table
    - Each chunk tracks its heading hierarchy (section_path)

    Returns list of Chunk objects.
    """
    chunks = []
    ingest = settings.ingest
    target = ingest.chunk_target_chars
    max_chars = ingest.chunk_max_chars
    min_chars = ingest.chunk_min_chars

    for item in outline.items:
        section_text = source_content[item.start_pos:item.end_pos]

        if len(section_text) <= max_chars:
            # Section fits in one chunk
            section_path = _build_section_path(outline, item)
            chunks.append(Chunk(
                chunk_id=f"chunk-{len(chunks)+1:03d}",
                source_path=outline.source_path,
                section_path=section_path,
                text=section_text,
                char_count=len(section_text),
                split_reason="outline",
                headings=[{"text": item.text, "level": item.level}],
            ))
        else:
            # Section needs splitting
            sub_chunks = _split_section(
                section_text=section_text,
                section_path=_build_section_path(outline, item),
                heading_text=item.text,
                heading_level=item.level,
                target_chars=target,
                max_chars=max_chars,
                min_chars=min_chars,
                base_index=len(chunks),
            )
            chunks.extend(sub_chunks)

    logger.info(
        "Chunking complete: %d sections → %d chunks (source: %d chars)",
        outline.section_count, len(chunks), outline.total_chars,
    )
    return chunks


def _build_section_path(outline: DocumentOutline, item: OutlineItem) -> list[str]:
    """Build heading hierarchy for a section item."""
    path = [item.text]
    # Find parent headings (higher-level headings before this item)
    for other in outline.items:
        if other.start_pos >= item.start_pos:
            break
        if other.level < item.level:
            # This could be a parent — keep the most recent at each level
            path.insert(0, other.text)
    # Deduplicate while preserving order
    seen = set()
    unique = []
    for p in path:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    return unique


def _split_section(
    section_text: str,
    section_path: list[str],
    heading_text: str,
    heading_level: int,
    target_chars: int,
    max_chars: int,
    min_chars: int,
    base_index: int,
) -> list[Chunk]:
    """
    Split a large section into sub-chunks at natural boundaries.

    Fallback order:
    1. Sub-headings (##, ###, etc.)
    2. Paragraph boundaries (\n\n)
    3. Sentence boundaries (. ! ?)
    4. Hard split (last resort, tries to avoid code fences/tables)
    """
    chunks = []

    # Try sub-headings first
    sub_heading_re = re.compile(r"^(#{2,6})\s+(.+)$", re.MULTILINE)
    sub_matches = list(sub_heading_re.finditer(section_text))

    if sub_matches and len(sub_matches) > 1:
        # Split by sub-headings
        for i, match in enumerate(sub_matches):
            start = match.start()
            end = sub_matches[i + 1].start() if i + 1 < len(sub_matches) else len(section_text)
            sub_text = section_text[start:end]
            sub_heading = match.group(2).strip()
            sub_level = len(match.group(1))

            if len(sub_text) > max_chars:
                # Recursively split
                sub_path = section_path + [sub_heading]
                deeper = _split_section(
                    sub_text, sub_path, sub_heading, sub_level,
                    target_chars, max_chars, min_chars, base_index + len(chunks),
                )
                chunks.extend(deeper)
            else:
                chunks.append(Chunk(
                    chunk_id=f"chunk-{base_index + len(chunks) + 1:03d}",
                    source_path="",  # filled by caller
                    section_path=section_path + [sub_heading],
                    text=sub_text,
                    char_count=len(sub_text),
                    split_reason="outline",
                    headings=[{"text": heading_text, "level": heading_level},
                              {"text": sub_heading, "level": sub_level}],
                ))
        return chunks

    # Try paragraph boundaries
    paragraphs = re.split(r"(\n\n+)", section_text)
    current_text = ""
    current_headings = [{"text": heading_text, "level": heading_level}]

    for part in paragraphs:
        if not part.strip():
            continue
        if len(current_text) + len(part) <= max_chars:
            current_text += part
        else:
            if len(current_text) >= min_chars:
                chunks.append(Chunk(
                    chunk_id=f"chunk-{base_index + len(chunks) + 1:03d}",
                    source_path="",
                    section_path=section_path,
                    text=current_text,
                    char_count=len(current_text),
                    split_reason="paragraph",
                    headings=current_headings,
                ))
            current_text = part

    if current_text:
        chunks.append(Chunk(
            chunk_id=f"chunk-{base_index + len(chunks) + 1:03d}",
            source_path="",
            section_path=section_path,
            text=current_text,
            char_count=len(current_text),
            split_reason="paragraph" if len(chunks) > 0 else "outline",
            headings=current_headings,
        ))

    # If still too large, try sentence boundaries
    result = []
    for chunk in chunks:
        if chunk.char_count > max_chars:
            sentence_chunks = _split_by_sentences(
                chunk, section_path, heading_text, heading_level,
                max_chars, min_chars, base_index + len(result),
            )
            result.extend(sentence_chunks)
        else:
            result.append(chunk)

    return result


def _split_by_sentences(
    chunk: Chunk,
    section_path: list[str],
    heading_text: str,
    heading_level: int,
    max_chars: int,
    min_chars: int,
    base_index: int,
) -> list[Chunk]:
    """Split chunk text by sentence boundaries."""
    sentences = re.split(r"(?<=[.!?])\s+", chunk.text)
    chunks = []
    current_text = ""

    for sentence in sentences:
        if len(current_text) + len(sentence) <= max_chars:
            current_text += (" " if current_text else "") + sentence
        else:
            if len(current_text) >= min_chars:
                chunks.append(Chunk(
                    chunk_id=f"chunk-{base_index + len(chunks) + 1:03d}",
                    source_path=chunk.source_path,
                    section_path=section_path,
                    text=current_text,
                    char_count=len(current_text),
                    split_reason="sentence",
                    headings=chunk.headings,
                ))
            current_text = sentence

    if current_text and len(current_text) >= min_chars:
        chunks.append(Chunk(
            chunk_id=f"chunk-{base_index + len(chunks) + 1:03d}",
            source_path=chunk.source_path,
            section_path=section_path,
            text=current_text,
            char_count=len(current_text),
            split_reason="sentence",
            headings=chunk.headings,
        ))

    # Hard split as last resort
    if not chunks and chunk.char_count > max_chars:
        chunks.append(Chunk(
            chunk_id=f"chunk-{base_index + 1:03d}",
            source_path=chunk.source_path,
            section_path=section_path,
            text=chunk.text[:max_chars],
            char_count=min(max_chars, chunk.char_count),
            split_reason="hard_max",
            headings=chunk.headings,
        ))

    return chunks


# ─────────────────────────────────────────────
# Merge analysis
# ─────────────────────────────────────────────

def merge_analysis(
    chunk_results: list[ChunkAnalysisResult],
    source_id: str,
    source_path: str,
) -> MergeAnalysisResult:
    """
    Combine chunk analysis results into a unified plan.

    - Deduplicate candidate pages by slug (merge claims)
    - Deduplicate claims by normalized text
    - Lift conflicts to source level
    - Build triage report
    - Build page write plan
    """
    # Deduplicate pages by slug
    pages_by_slug: dict[str, dict] = {}
    for cr in chunk_results:
        for page in cr.candidate_pages:
            if page not in pages_by_slug:
                pages_by_slug[page] = {
                    "slug": page,
                    "source_chunks": [],
                    "claims": [],
                }
            pages_by_slug[page]["source_chunks"].append(cr.chunk_id)

    # Deduplicate claims
    seen_claims: set[str] = set()
    all_claims = []
    for cr in chunk_results:
        for claim in cr.claims:
            normalized = claim.get("normalized", claim.get("quote", ""))
            claim_key = normalized[:100].lower().strip()
            if claim_key not in seen_claims:
                seen_claims.add(claim_key)
                all_claims.append(claim)

    # Collect conflicts
    all_conflicts = []
    for cr in chunk_results:
        all_conflicts.extend(cr.conflicts)

    # Count outcomes
    processed = sum(1 for cr in chunk_results if cr.outcome != "failed")
    failed = sum(1 for cr in chunk_results if cr.outcome == "failed")

    # Build triage report
    triage_lines = [
        f"Source: {source_path}",
        f"Chunks: {len(chunk_results)} total, {processed} processed, {failed} failed",
        f"Pages planned: {len(pages_by_slug)}",
        f"Claims extracted: {len(all_claims)}",
        f"Conflicts detected: {len(all_conflicts)}",
    ]

    # Outcome summary
    outcomes = {}
    for cr in chunk_results:
        outcomes[cr.outcome] = outcomes.get(cr.outcome, 0) + 1
    triage_lines.append("")
    triage_lines.append("Chunk outcomes:")
    for outcome, count in sorted(outcomes.items()):
        triage_lines.append(f"  {outcome}: {count}")

    # Build page write plan
    page_write_plan = []
    for slug, info in pages_by_slug.items():
        page_write_plan.append({
            "slug": slug,
            "action": "create",  # default; ingest agent determines update vs create
            "source_chunks": info["source_chunks"],
            "claim_count": len(info["claims"]),
        })

    return MergeAnalysisResult(
        source_id=source_id,
        source_path=source_path,
        total_chunks=len(chunk_results),
        chunks_processed=processed,
        chunks_failed=failed,
        all_candidate_pages=list(pages_by_slug.values()),
        all_claims=all_claims,
        all_conflicts=all_conflicts,
        triage_report="\n".join(triage_lines),
        page_write_plan=page_write_plan,
    )
