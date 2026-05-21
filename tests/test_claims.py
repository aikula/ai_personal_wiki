"""
test_claims.py — Tests for the claims layer (Phase 3).
"""

import pytest

from app.agents.ingest_agent import IngestAgent
from app.config import Settings
from app.core.wiki_fs import Claim, WikiFS


@pytest.fixture
def settings(tmp_path):
    s = Settings()
    s.wiki_data_path = str(tmp_path)
    return s


@pytest.fixture
def fs(settings):
    return WikiFS(settings)


@pytest.fixture
def ingest_agent(fs, settings):
    return IngestAgent(fs, llm=None, interpreter=None, settings=settings)


def _make_claim(**overrides):
    base = {
        "claim_id": "myproj/source#chunk-001-claim-001",
        "source_id": "myproj/source",
        "source_path": "raw/myproj/source.md",
        "source_sha256": "abc123",
        "source_section": "## Features",
        "quote": "Redis 7.2 is used for session cache",
        "normalized": "Redis 7.2 используется для session cache.",
        "related_slugs": ["myproj/storage/redis"],
        "confidence": 0.92,
        "status": "active",
        "chunk_id": "chunk-001",
        "project": "myproj",
        "created": "2026-05-20",
    }
    base.update(overrides)
    return Claim(**base)


# ── Claim write/read ────────────────────────────────────────────

class TestClaimWriteRead:
    def test_write_and_read_claim(self, fs):
        claim = _make_claim()
        path = fs.write_claim(claim)

        assert path.exists()
        content = path.read_text()
        assert "claim_id" in content
        assert "Redis 7.2" in content

        read = fs.read_claim(
            claim_id=claim.claim_id,
            project="myproj",
            source_id="myproj/source",
            chunk_id="chunk-001",
        )
        assert read is not None
        assert read.quote == "Redis 7.2 is used for session cache"
        assert read.status == "active"
        assert read.confidence == 0.92

    def test_read_nonexistent_claim(self, fs):
        result = fs.read_claim("nonexistent", "myproj", "source", "chunk-001")
        assert result is None

    def test_claim_file_structure(self, fs):
        claim = _make_claim()
        path = fs.write_claim(claim)

        # Should be under wiki/_claims/<project>/<source>/<chunk>/
        assert "_claims" in str(path)
        assert "myproj" in str(path)


class TestIngestClaimPersistence:
    def test_persist_claims_writes_claim_files(self, ingest_agent, fs):
        written = ingest_agent._persist_claims(
            [{
                "quote": "Fact from chunk",
                "normalized": "Fact from chunk normalized",
                "source_section": "## Chunk",
                "related_slugs": ["proj/page"],
                "confidence": 0.8,
                "chunk_id": "chunk-001",
            }],
            source_id="proj/source",
            project="proj",
            raw_relative_path="proj/source.md",
            source_sha256="sha",
        )

        assert len(written) == 1
        claims = fs.list_claims(source_id="proj/source")
        assert len(claims) == 1
        assert claims[0].related_slugs == ["proj/page"]


# ── List claims ─────────────────────────────────────────────────

class TestListClaims:
    def test_list_all_claims(self, fs):
        claim1 = _make_claim(claim_id="myproj/s#chunk-001-claim-001")
        claim2 = _make_claim(
            claim_id="myproj/s#chunk-002-claim-001",
            chunk_id="chunk-002",
            related_slugs=["myproj/other"],
        )
        fs.write_claim(claim1)
        fs.write_claim(claim2)

        claims = fs.list_claims()
        assert len(claims) == 2

    def test_list_claims_by_project(self, fs):
        claim_a = _make_claim(claim_id="proja/s#chunk-001-claim-001", project="proja", source_id="proja/s")
        claim_b = _make_claim(claim_id="projb/s#chunk-001-claim-001", project="projb", source_id="projb/s")
        fs.write_claim(claim_a)
        fs.write_claim(claim_b)

        proja_claims = fs.list_claims(project="proja")
        assert len(proja_claims) == 1
        assert proja_claims[0].project == "proja"

    def test_list_claims_by_source(self, fs):
        claim1 = _make_claim(claim_id="myproj/s1#chunk-001-claim-001", source_id="myproj/s1")
        claim2 = _make_claim(claim_id="myproj/s2#chunk-001-claim-001", source_id="myproj/s2")
        fs.write_claim(claim1)
        fs.write_claim(claim2)

        s1_claims = fs.list_claims(source_id="myproj/s1")
        assert len(s1_claims) == 1
        assert s1_claims[0].source_id == "myproj/s1"

    def test_list_claims_by_status(self, fs):
        active = _make_claim(claim_id="myproj/s#chunk-001-claim-001", status="active")
        ignored = _make_claim(claim_id="myproj/s#chunk-001-claim-002", status="ignored", related_slugs=[])
        fs.write_claim(active)
        fs.write_claim(ignored)

        active_claims = fs.list_claims(status="active")
        assert len(active_claims) == 1
        assert active_claims[0].status == "active"

    def test_list_empty_claims(self, fs):
        assert fs.list_claims() == []


# ── Claim deduplication ─────────────────────────────────────────

class TestClaimDeduplication:
    def test_find_duplicate_claim(self, fs):
        claim = _make_claim()
        fs.write_claim(claim)

        dup = fs.find_duplicate_claim(
            normalized="Redis 7.2 используется для session cache.",
            source_id="myproj/source",
        )
        assert dup is not None
        assert dup.claim_id == claim.claim_id

    def test_no_duplicate_different_normalized(self, fs):
        claim = _make_claim()
        fs.write_claim(claim)

        dup = fs.find_duplicate_claim(
            normalized="PostgreSQL is the main database.",
            source_id="myproj/source",
        )
        assert dup is None

    def test_no_duplicate_different_source(self, fs):
        claim = _make_claim()
        fs.write_claim(claim)

        dup = fs.find_duplicate_claim(
            normalized="Redis 7.2 используется для session cache.",
            source_id="other/source",
        )
        assert dup is None


# ── Claim status updates ────────────────────────────────────────

class TestClaimStatus:
    def test_update_claim_status(self, fs):
        claim = _make_claim()
        fs.write_claim(claim)

        result = fs.update_claim_status(
            claim_id=claim.claim_id,
            project="myproj",
            source_id="myproj/source",
            chunk_id="chunk-001",
            new_status="superseded",
        )
        assert result is True

        read = fs.read_claim(claim.claim_id, "myproj", "myproj/source", "chunk-001")
        assert read.status == "superseded"

    def test_update_nonexistent_claim(self, fs):
        result = fs.update_claim_status(
            claim_id="nonexistent",
            project="myproj",
            source_id="myproj/source",
            chunk_id="chunk-001",
            new_status="ignored",
        )
        assert result is False


# ── Claims for pages ────────────────────────────────────────────

class TestClaimsForPages:
    def test_get_claims_for_page(self, fs):
        claim1 = _make_claim(related_slugs=["myproj/redis", "myproj/cache"])
        claim2 = _make_claim(
            claim_id="myproj/s#chunk-001-claim-002",
            related_slugs=["myproj/postgres"],
        )
        fs.write_claim(claim1)
        fs.write_claim(claim2)

        redis_claims = fs.get_claims_for_page("myproj/redis")
        assert len(redis_claims) == 1
        assert redis_claims[0].claim_id == claim1.claim_id

    def test_get_claims_for_page_no_matches(self, fs):
        claim = _make_claim(related_slugs=["myproj/redis"])
        fs.write_claim(claim)

        claims = fs.get_claims_for_page("myproj/nonexistent")
        assert claims == []


# ── Claim conflict detection ────────────────────────────────────

class TestClaimConflictDetection:
    def test_detect_conflicting_claims_same_page(self, fs):
        claim1 = _make_claim(
            claim_id="myproj/s#chunk-001-claim-001",
            source_id="myproj/s",
            source_path="raw/myproj/s.md",
            related_slugs=["myproj/redis"],
            confidence=0.9,
            normalized="Redis 7.2 is used.",
        )
        claim2 = _make_claim(
            claim_id="myproj/s#chunk-002-claim-001",
            source_id="myproj/s",
            source_path="raw/myproj/s.md",
            related_slugs=["myproj/redis"],
            confidence=0.85,
            normalized="Redis 6.0 is used.",
        )
        fs.write_claim(claim1)
        fs.write_claim(claim2)

        conflicts = fs.detect_claim_conflicts("myproj/s")
        assert len(conflicts) == 1
        assert conflicts[0][0].claim_id == claim1.claim_id
        assert conflicts[0][1].claim_id == claim2.claim_id

    def test_no_conflict_different_pages(self, fs):
        claim1 = _make_claim(
            source_id="myproj/s", source_path="raw/myproj/s.md",
            related_slugs=["myproj/redis"], confidence=0.9,
        )
        claim2 = _make_claim(
            claim_id="myproj/s#chunk-002-claim-001",
            source_id="myproj/s", source_path="raw/myproj/s.md",
            related_slugs=["myproj/postgres"], confidence=0.9,
        )
        fs.write_claim(claim1)
        fs.write_claim(claim2)

        conflicts = fs.detect_claim_conflicts("myproj/s")
        assert len(conflicts) == 0

    def test_no_conflict_low_confidence(self, fs):
        claim1 = _make_claim(
            source_id="myproj/s", source_path="raw/myproj/s.md",
            related_slugs=["myproj/redis"], confidence=0.5,
        )
        claim2 = _make_claim(
            claim_id="myproj/s#chunk-002-claim-001",
            source_id="myproj/s", source_path="raw/myproj/s.md",
            related_slugs=["myproj/redis"], confidence=0.5,
        )
        fs.write_claim(claim1)
        fs.write_claim(claim2)

        conflicts = fs.detect_claim_conflicts("myproj/s")
        assert len(conflicts) == 0


# ── Claim provenance in wiki pages ──────────────────────────────

class TestClaimProvenance:
    def test_claim_contains_source_reference(self, fs):
        claim = _make_claim()
        path = fs.write_claim(claim)
        content = path.read_text()

        assert claim.source_path in content
        assert claim.source_section in content
        assert claim.quote in content

    def test_claim_contains_related_wikilinks(self, fs):
        claim = _make_claim(related_slugs=["myproj/redis", "myproj/cache"])
        path = fs.write_claim(claim)
        content = path.read_text()

        assert "[[myproj/redis]]" in content
        assert "[[myproj/cache]]" in content
