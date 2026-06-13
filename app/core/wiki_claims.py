"""
wiki_claims.py — Claim operations for WikiFS.
"""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import frontmatter
from rapidfuzz import fuzz as _fuzz

from app.core.wiki_types import Claim

logger = logging.getLogger("wiki.claims")


def claim_path(fs, claim: Claim) -> Path:
    """Get absolute path for a claim file."""
    return fs._resolve_in_dir(fs.wiki_dir, claim.file_path)


def write_claim(fs, claim: Claim) -> Path:
    """Write a claim to wiki/_claims/. Returns the written path."""
    path = claim_path(fs, claim)
    path.parent.mkdir(parents=True, exist_ok=True)

    meta = {
        "title": f"Claim: {claim.claim_id}",
        "project": claim.project,
        "type": "concept",
        "tags": ["claim", claim.source_id, claim.status],
        "confidence": claim.confidence,
        "sources": 1,
        "last_confirmed": claim.created,
        "supersedes": None,
        "superseded_by": None,
        "created": claim.created,
        "claim_id": claim.claim_id,
        "source_id": claim.source_id,
        "source_path": claim.source_path,
        "source_sha256": claim.source_sha256,
        "source_section": claim.source_section,
        "quote": claim.quote,
        "normalized": claim.normalized,
        "related_slugs": claim.related_slugs,
        "status": claim.status,
        "chunk_id": claim.chunk_id,
    }

    content = (
        f"# {claim.claim_id}\n\n"
        f"**Source:** `{claim.source_path}`\n"
        f"**Section:** {claim.source_section}\n"
        f"**Status:** {claim.status}\n\n"
        f"## Quote\n\n"
        f"> {claim.quote}\n\n"
        f"## Normalized\n\n"
        f"{claim.normalized}\n\n"
        f"## Related Pages\n\n"
        + "\n".join(f"- [[{s}]]" for s in claim.related_slugs)
        + "\n"
    )

    post = frontmatter.Post(content, **meta)
    raw = frontmatter.dumps(post)
    path.write_text(raw, encoding="utf-8")
    logger.debug("Claim written: id=%s status=%s", claim.claim_id, claim.status)
    return path


def read_claim(fs, claim_id: str, project: str, source_id: str, chunk_id: str) -> Claim | None:
    """Read a specific claim by its identifiers."""
    safe_source = source_id.replace("/", "__")
    rel_path = f"_claims/{project}/{safe_source}/{chunk_id}/{claim_id.split('#')[-1]}.md"
    path = fs._resolve_in_dir(fs.wiki_dir, rel_path)
    if not path.exists():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
        post = frontmatter.loads(raw)
        meta = post.metadata

        def _date_or(field: str, default: str) -> str:
            val = meta.get(field, default)
            return val.isoformat() if isinstance(val, date) else (val or default)

        return Claim(
            claim_id=meta.get("claim_id", claim_id),
            source_id=meta.get("source_id", source_id),
            source_path=meta.get("source_path", ""),
            source_sha256=meta.get("source_sha256", ""),
            source_section=meta.get("source_section", ""),
            quote=meta.get("quote", ""),
            normalized=meta.get("normalized", ""),
            related_slugs=meta.get("related_slugs", []),
            confidence=float(meta.get("confidence", 1.0)),
            status=meta.get("status", "active"),
            chunk_id=meta.get("chunk_id", chunk_id),
            project=project,
            created=_date_or("created", date.today().isoformat()),
        )
    except Exception as exc:
        logger.error("Claim parse error: id=%s error=%s", claim_id, exc)
        return None


def list_claims(
    fs,
    project: str | None = None,
    status: str | None = None,
    source_id: str | None = None,
) -> list[Claim]:
    """List claims with optional filters."""
    claims = []
    claims_dir = fs.wiki_dir / "_claims"
    if not claims_dir.exists():
        return claims

    for path in sorted(claims_dir.rglob("*.md")):
        try:
            raw = path.read_text(encoding="utf-8")
            post = frontmatter.loads(raw)
            meta = post.metadata

            claim_project = meta.get("project", "_general")
            claim_source = meta.get("source_id", "")
            claim_status = meta.get("status", "active")

            if project and claim_project != project:
                continue
            if source_id and claim_source != source_id:
                continue
            if status and claim_status != status:
                continue

            created_val = meta.get("created", date.today().isoformat())
            created = created_val.isoformat() if isinstance(created_val, date) else (created_val or date.today().isoformat())

            claims.append(Claim(
                claim_id=meta.get("claim_id", ""),
                source_id=claim_source,
                source_path=meta.get("source_path", ""),
                source_sha256=meta.get("source_sha256", ""),
                source_section=meta.get("source_section", ""),
                quote=meta.get("quote", ""),
                normalized=meta.get("normalized", ""),
                related_slugs=meta.get("related_slugs", []),
                confidence=float(meta.get("confidence", 1.0)),
                status=claim_status,
                chunk_id=meta.get("chunk_id", ""),
                project=claim_project,
                created=created,
            ))
        except Exception as exc:
            logger.debug("Skipping claim file %s: %s", path, exc)

    return claims


def search_claims(fs, query: str, project: str | None = None, top_k: int = 10) -> list[Claim]:
    """Fuzzy search claims by normalized text. Returns top_k matches."""
    all_claims = list_claims(fs, project=project, status="active")
    if not all_claims:
        return []

    query_lower = query.lower().strip()
    scored: list[tuple[int, Claim]] = []
    for claim in all_claims:
        normalized = (claim.normalized or claim.quote or "").lower()
        if not normalized:
            continue
        if abs(len(normalized) - len(query_lower)) > len(query_lower) * 2:
            continue
        score = _fuzz.partial_ratio(query_lower, normalized)
        if score > 60:
            scored.append((score, claim))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [claim for _, claim in scored[:top_k]]


def find_duplicate_claim(fs, normalized: str, source_id: str) -> Claim | None:
    """
    Check if a similar claim already exists (fuzzy match across all sources).
    Uses rapidfuzz with threshold 85 for near-duplicate detection.
    """
    normalized_lower = normalized.lower().strip()
    norm_len = len(normalized_lower)
    threshold = 85
    best_match: Claim | None = None
    best_score = 0

    all_claims = list_claims(fs, status="active")
    for claim in all_claims:
        claim_lower = claim.normalized.lower().strip()
        if abs(len(claim_lower) - norm_len) > norm_len * 0.4:
            continue
        score = _fuzz.ratio(normalized_lower, claim_lower)
        if score > threshold and score > best_score:
            best_score = score
            best_match = claim

    return best_match


def update_claim_status(fs, claim_id: str, project: str, source_id: str, chunk_id: str, new_status: str) -> bool:
    """Update the status of an existing claim."""
    claim = read_claim(fs, claim_id, project, source_id, chunk_id)
    if claim is None:
        return False
    claim.status = new_status
    write_claim(fs, claim)
    return True


def get_claims_for_page(fs, slug: str) -> list[Claim]:
    """Get all active claims that reference a given wiki page."""
    all_claims = list_claims(fs, status="active")
    return [c for c in all_claims if slug in c.related_slugs]


def detect_claim_conflicts(fs, source_id: str) -> list[tuple[Claim, Claim]]:
    """
    Detect conflicting claims within the same source.
    Returns list of (claim_a, claim_b) pairs where claims have
    contradictory statuses or normalized text suggests contradiction.
    """
    conflicts = []
    claims = list_claims(fs, source_id=source_id, status="active")

    for i, claim_a in enumerate(claims):
        for claim_b in claims[i + 1:]:
            common_slugs = set(claim_a.related_slugs) & set(claim_b.related_slugs)
            if common_slugs:
                if claim_a.confidence > 0.7 and claim_b.confidence > 0.7:
                    conflicts.append((claim_a, claim_b))

    return conflicts
