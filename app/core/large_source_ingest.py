"""Outline parser, chunking, and merge analysis for large source ingest."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from app.config import Settings

logger = logging.getLogger("wiki.ingest.large")

@dataclass
class OutlineItem:
    text: str
    level: int
    start_pos: int
    end_pos: int
    char_count: int
    preview: str = ""


@dataclass
class DocumentOutline:
    source_path: str
    total_chars: int
    items: list[OutlineItem] = field(default_factory=list)

    @property
    def section_count(self) -> int:
        return len(self.items)


@dataclass
class Chunk:
    chunk_id: str
    source_path: str
    section_path: list[str]
    text: str
    char_count: int
    split_reason: str = "outline"
    headings: list[dict] = field(default_factory=list)


@dataclass
class ChunkAnalysisResult:
    chunk_id: str
    source_id: str
    section_path: list[str]
    candidate_pages: list[str] = field(default_factory=list)
    page_sections: dict[str, list[str]] = field(default_factory=dict)
    claims: list[dict] = field(default_factory=list)
    conflicts: list[dict] = field(default_factory=list)
    ignored_sections: list[str] = field(default_factory=list)
    outcome: str = "pending"


@dataclass
class MergeAnalysisResult:
    source_id: str
    source_path: str
    total_chunks: int
    chunks_processed: int
    chunks_failed: int
    all_candidate_pages: list[dict] = field(default_factory=list)
    all_claims: list[dict] = field(default_factory=list)
    all_conflicts: list[dict] = field(default_factory=list)
    triage_report: str = ""
    page_write_plan: list[dict] = field(default_factory=list)

def parse_outline(source_content: str, source_path: str = "", target_chars: int = 16000) -> DocumentOutline:
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

    # Fallback: paragraph groups sized to target_chars
    items = _parse_paragraph_groups(source_content, min_chars=target_chars)
    if items:
        return DocumentOutline(
            source_path=source_path,
            total_chars=len(source_content),
            items=items,
        )

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
    heading_re = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
    matches = list(heading_re.finditer(content))

    if not matches:
        return []

    items = []
    for i, match in enumerate(matches):
        level = len(match.group(1))
        text = match.group(2).strip()
        start = match.end()

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
    """Group consecutive paragraphs into outline sections of ~min_chars."""
    paragraphs = re.split(r"\n\n+", content)
    paragraphs = [p.strip() for p in paragraphs if p.strip()]

    if not paragraphs:
        return []

    # Find actual positions in source content
    para_positions: list[tuple[str, int, int]] = []
    search_start = 0
    for para in paragraphs:
        start = content.find(para, search_start)
        if start == -1:
            start = search_start
        end = start + len(para)
        para_positions.append((para, start, end))
        search_start = end

    items = []
    group_paras: list[tuple[str, int, int]] = []
    group_chars = 0

    def flush_group():
        if not group_paras:
            return
        text = "\n\n".join(p for p, _, _ in group_paras)
        first_sentence = group_paras[0][0].split(".")[0][:80]
        title = first_sentence if first_sentence else f"Section at char {group_paras[0][1]}"
        items.append(OutlineItem(
            text=title,
            level=2,
            start_pos=group_paras[0][1],
            end_pos=group_paras[-1][2],
            char_count=group_chars,
            preview=text[:200].replace("\n", " "),
        ))

    for para, start, end in para_positions:
        if group_chars + len(para) > min_chars * 2 and group_chars >= min_chars:
            flush_group()
            group_paras = []
            group_chars = 0
        group_paras.append((para, start, end))
        group_chars += len(para)

    flush_group()
    return items

def chunk_by_outline(
    outline: DocumentOutline,
    source_content: str,
    settings: Settings,
) -> list[Chunk]:
    chunks = []
    ingest = settings.ingest
    target = ingest.chunk_target_chars
    max_chars = ingest.chunk_max_chars
    min_chars = ingest.chunk_min_chars
    overlap = ingest.chunk_overlap_chars

    previous_tail = ""
    for item in outline.items:
        section_text = source_content[item.start_pos:item.end_pos]

        if len(section_text) <= max_chars:
            section_path = _build_section_path(outline, item)
            text = section_text
            if previous_tail and len(text) > overlap:
                text = previous_tail + "\n--- CONTINUED ---\n" + text
            previous_tail = section_text[-overlap:] if overlap and len(section_text) > overlap else ""
            chunks.append(Chunk(
                chunk_id=f"chunk-{len(chunks)+1:03d}",
                source_path=outline.source_path,
                section_path=section_path,
                text=text,
                char_count=len(text),
                split_reason="outline",
                headings=[{"text": item.text, "level": item.level}],
            ))
        else:
            sub_chunks = _split_section(
                section_text=section_text,
                section_path=_build_section_path(outline, item),
                source_path=outline.source_path,
                heading_text=item.text,
                heading_level=item.level,
                target_chars=target,
                max_chars=max_chars,
                min_chars=min_chars,
                base_index=len(chunks),
                overlap=overlap,
                previous_tail=previous_tail,
            )
            if sub_chunks:
                previous_tail = sub_chunks[-1].text[-overlap:] if overlap and len(sub_chunks[-1].text) > overlap else ""
            chunks.extend(sub_chunks)

    logger.info(
        "Chunking complete: %d sections → %d chunks (source: %d chars)",
        outline.section_count, len(chunks), outline.total_chars,
    )
    return chunks


def _build_section_path(outline: DocumentOutline, item: OutlineItem) -> list[str]:
    """Build heading hierarchy for a section item."""
    stack: list[OutlineItem] = []
    for other in outline.items:
        if other.start_pos >= item.start_pos:
            break
        while stack and stack[-1].level >= other.level:
            stack.pop()
        stack.append(other)
    parents = [h.text for h in stack if h.level < item.level]
    return [*parents, item.text]


def _split_section(
    section_text: str,
    section_path: list[str],
    source_path: str,
    heading_text: str,
    heading_level: int,
    target_chars: int,
    max_chars: int,
    min_chars: int,
    base_index: int,
    overlap: int = 0,
    previous_tail: str = "",
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
                sub_path = [*section_path, sub_heading]
                first_tail = previous_tail if i == 0 and previous_tail else ""
                deeper = _split_section(
                    sub_text, sub_path, source_path, sub_heading, sub_level,
                    target_chars, max_chars, min_chars, base_index + len(chunks),
                    overlap=overlap, previous_tail=first_tail,
                )
                chunks.extend(deeper)
            else:
                text = sub_text
                if i == 0 and previous_tail and overlap and len(text) > overlap:
                    text = previous_tail + "\n--- CONTINUED ---\n" + text
                chunks.append(Chunk(
                    chunk_id=f"chunk-{base_index + len(chunks) + 1:03d}",
                    source_path=source_path,
                    section_path=[*section_path, sub_heading],
                    text=text,
                    char_count=len(text),
                    split_reason="outline",
                    headings=[{"text": heading_text, "level": heading_level},
                              {"text": sub_heading, "level": sub_level}],
                ))
        return chunks

    # Try paragraph boundaries
    paragraphs = re.split(r"(\n\n+)", section_text)
    current_text = previous_tail + "\n--- CONTINUED ---\n" if previous_tail and overlap else ""
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
                    source_path=source_path,
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
            source_path=source_path,
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
                    "source_sections": [],
                }
            pages_by_slug[page]["source_chunks"].append(cr.chunk_id)
            if len(pages_by_slug[page]["source_chunks"]) > 1:
                logger.warning(
                    "Merge collision: slug '%s' planned by multiple chunks %s",
                    page, pages_by_slug[page]["source_chunks"],
                )
            for section in cr.page_sections.get(page, []):
                if section and section not in pages_by_slug[page]["source_sections"]:
                    pages_by_slug[page]["source_sections"].append(section)

    # Deduplicate claims (fuzzy match within same source)
    from rapidfuzz import fuzz as _fuzz

    seen_claims: list[str] = []  # store normalized texts for fuzzy comparison
    all_claims = []
    for cr in chunk_results:
        for claim in cr.claims:
            normalized = claim.get("normalized", claim.get("quote", "")).lower().strip()
            is_dup = False
            for seen in seen_claims:
                if abs(len(seen) - len(normalized)) < max(len(normalized), 1) * 0.4:
                    if _fuzz.ratio(normalized, seen) > 85:
                        is_dup = True
                        break
            if not is_dup:
                seen_claims.append(normalized)
                all_claims.append(claim)
            for slug in claim.get("related_slugs", []):
                if slug in pages_by_slug and claim not in pages_by_slug[slug]["claims"]:
                    pages_by_slug[slug]["claims"].append(claim)

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
