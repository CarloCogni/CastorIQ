# writeback/tests/test_conflict_scan_service.py
"""Tests for ConflictScanService — LLM always mocked, pgvector operations patched."""

import json
from unittest.mock import MagicMock, patch

import pytest

from writeback.models import Conflict, ScanRun
from writeback.services.conflict_scan_service import ConflictScanService


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_llm(monkeypatch):
    """Mock LLM that returns a canned conflict JSON response."""
    llm = MagicMock()
    llm.invoke.return_value.content = json.dumps(
        {
            "conflicts": [
                {
                    "property_name": "FireRating",
                    "ifc_value": "EI60",
                    "document_value": "EI120",
                    "title": "Fire rating below requirement",
                    "description": "Wall fire rating is EI60 but specification requires EI120.",
                    "severity": "critical",
                    "confidence": 0.92,
                    "suggested_fix": "Set FireRating from EI60 to EI120",
                    "source_chunk_index": 0,
                }
            ]
        }
    )
    monkeypatch.setattr(
        "writeback.services.conflict_scan_service.get_llm",
        lambda **kwargs: llm,
    )
    return llm


@pytest.fixture
def scan_service(project, user, mock_llm):
    """ConflictScanService with LLM already patched."""
    return ConflictScanService(project, user, skip_low_value=True)


# ── _format_properties ─────────────────────────────────────────────────────────


class TestFormatProperties:
    """Tests for ConflictScanService._format_properties() — pure, no DB needed."""

    def test_empty_dict_returns_no_properties_message(self):
        """Empty properties dict returns the '(no properties)' sentinel."""
        result = ConflictScanService._format_properties({})
        assert result == "(no properties)"

    def test_flat_properties_formatted_as_key_value(self):
        """Flat properties (key: value) are formatted as '- key: value' lines."""
        result = ConflictScanService._format_properties({"FireRating": "EI60"})
        assert "FireRating" in result
        assert "EI60" in result

    def test_nested_pset_dict_formats_with_header(self):
        """Nested pset dicts are formatted with '**pset:**' header."""
        result = ConflictScanService._format_properties(
            {"Pset_WallCommon": {"FireRating": "EI60", "IsExternal": True}}
        )
        assert "Pset_WallCommon" in result
        assert "FireRating" in result
        assert "EI60" in result

    def test_none_properties_returns_no_properties_message(self):
        """None/falsy properties returns '(no properties)'."""
        result = ConflictScanService._format_properties(None)
        assert result == "(no properties)"


# ── _get_requirement_chunks ────────────────────────────────────────────────────


@pytest.mark.django_db
class TestGetRequirementChunks:
    """Tests for _get_requirement_chunks() — DB needed for DocumentChunk queries."""

    def test_returns_empty_list_when_no_documents(self, scan_service):
        """No documents in project → empty list returned."""
        result = scan_service._get_requirement_chunks()
        assert result == []

    def test_returns_chunks_containing_requirement_keywords(self, scan_service, project):
        """Chunks with AEC keywords are returned; non-matching are excluded."""
        from documents.models import Document, DocumentChunk
        from ifc_processor.tests.factories import IFCFileFactory

        # Create a completed document with an embedding
        doc = Document.objects.create(
            project=project,
            name="Spec.pdf",
            file="spec.pdf",
            status="completed",
        )
        # Chunk with keyword "shall"
        DocumentChunk.objects.create(
            document=doc,
            content="Walls shall have a fire rating of EI120.",
            chunk_index=0,
            embedding=[0.1] * 1024,
        )
        # Chunk without any keyword
        DocumentChunk.objects.create(
            document=doc,
            content="This is an introduction.",
            chunk_index=1,
            embedding=[0.2] * 1024,
        )

        result = scan_service._get_requirement_chunks()

        contents = [c.content for c in result]
        assert any("shall" in c for c in contents)
        assert all("introduction" not in c for c in contents)


# ── _upsert_conflict ──────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestUpsertConflict:
    """Tests for _upsert_conflict() — creates, updates, or skips Conflict records."""

    def _make_finding(self):
        return {
            "property_name": "FireRating",
            "ifc_value": "EI60",
            "document_value": "EI120",
            "title": "Fire rating below requirement",
            "description": "Wall is EI60 but spec says EI120.",
            "severity": "critical",
            "confidence": 0.92,
            "suggested_fix": "Set FireRating to EI120",
        }

    def test_upsert_creates_new_conflict(self, scan_service, project, ifc_file):
        """First call creates a new OPEN Conflict record."""
        from ifc_processor.tests.factories import IFCEntityFactory
        from documents.models import Document, DocumentChunk

        entity = IFCEntityFactory(ifc_file=ifc_file, ifc_type="IfcWall")
        doc = Document.objects.create(
            project=project, name="Spec.pdf", file="spec.pdf", status="completed"
        )
        chunk = DocumentChunk.objects.create(
            document=doc, content="Walls shall have EI120.", chunk_index=0
        )
        scan_run = ScanRun.objects.create(
            project=project,
            triggered_by=scan_service.user,
            scan_type=ScanRun.ScanType.FULL,
            status=ScanRun.Status.RUNNING,
            llm_model_used="test",
        )

        result = scan_service._upsert_conflict(entity, chunk, self._make_finding(), scan_run)

        assert result == "created"
        assert Conflict.objects.filter(project=project, status=Conflict.Status.OPEN).exists()

    def test_upsert_updates_existing_open_conflict(self, scan_service, project, ifc_file):
        """Second call with same hash updates the existing OPEN conflict."""
        from ifc_processor.tests.factories import IFCEntityFactory
        from documents.models import Document, DocumentChunk
        import hashlib

        entity = IFCEntityFactory(ifc_file=ifc_file, ifc_type="IfcWall")
        doc = Document.objects.create(
            project=project, name="Spec.pdf", file="spec.pdf", status="completed"
        )
        chunk = DocumentChunk.objects.create(
            document=doc, content="Walls shall have EI120.", chunk_index=0
        )
        scan_run = ScanRun.objects.create(
            project=project,
            triggered_by=scan_service.user,
            scan_type=ScanRun.ScanType.FULL,
            status=ScanRun.Status.RUNNING,
            llm_model_used="test",
        )

        finding = self._make_finding()

        # First call — create
        scan_service._upsert_conflict(entity, chunk, finding, scan_run)

        # Second call — update
        finding["description"] = "Updated description."
        result = scan_service._upsert_conflict(entity, chunk, finding, scan_run)

        assert result == "updated"
        conflict = Conflict.objects.get(project=project)
        assert conflict.description == "Updated description."

    def test_upsert_skips_dismissed_conflict(self, scan_service, project, ifc_file):
        """Dismissed conflicts are never recreated."""
        from ifc_processor.tests.factories import IFCEntityFactory
        from documents.models import Document, DocumentChunk
        import hashlib

        entity = IFCEntityFactory(ifc_file=ifc_file, ifc_type="IfcWall")
        doc = Document.objects.create(
            project=project, name="Spec.pdf", file="spec.pdf", status="completed"
        )
        chunk = DocumentChunk.objects.create(
            document=doc, content="Walls shall have EI120.", chunk_index=0
        )
        scan_run = ScanRun.objects.create(
            project=project,
            triggered_by=scan_service.user,
            scan_type=ScanRun.ScanType.FULL,
            status=ScanRun.Status.RUNNING,
            llm_model_used="test",
        )

        finding = self._make_finding()
        content_hash = hashlib.sha256(f"{entity.id}:{chunk.id}:FireRating".encode()).hexdigest()

        Conflict.objects.create(
            project=project,
            ifc_entity=entity,
            document_chunk=chunk,
            content_hash=content_hash,
            status=Conflict.Status.DISMISSED,
            title="Old conflict",
            description="Already dismissed.",
            severity=Conflict.Severity.LOW,
        )

        result = scan_service._upsert_conflict(entity, chunk, finding, scan_run)

        assert result == "skipped"


# ── _evaluate_entity ──────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestEvaluateEntity:
    """Tests for _evaluate_entity() — LLM mocked."""

    def test_evaluate_entity_returns_findings(self, scan_service, ifc_file):
        """Valid LLM response returns list of conflict dicts."""
        from ifc_processor.tests.factories import IFCEntityFactory
        from documents.models import Document, DocumentChunk

        entity = IFCEntityFactory(
            ifc_file=ifc_file,
            ifc_type="IfcWall",
            properties={"Pset_WallCommon.FireRating": "EI60"},
        )
        doc = Document.objects.create(
            project=scan_service.project,
            name="Spec.pdf",
            file="spec.pdf",
            status="completed",
        )
        chunk = DocumentChunk.objects.create(
            document=doc, content="Walls shall have EI120.", chunk_index=0
        )

        findings = scan_service._evaluate_entity(entity, [chunk])

        assert isinstance(findings, list)
        assert len(findings) >= 1
        assert findings[0]["property_name"] == "FireRating"

    def test_evaluate_entity_invalid_json_returns_empty_list(self, scan_service, ifc_file):
        """If the LLM returns invalid JSON, _evaluate_entity returns [] gracefully."""
        from ifc_processor.tests.factories import IFCEntityFactory
        from documents.models import Document, DocumentChunk

        entity = IFCEntityFactory(ifc_file=ifc_file, ifc_type="IfcWall")
        doc = Document.objects.create(
            project=scan_service.project,
            name="Spec.pdf",
            file="spec.pdf",
            status="completed",
        )
        chunk = DocumentChunk.objects.create(document=doc, content="Some content.", chunk_index=0)

        # Override mock to return bad JSON
        scan_service.llm.invoke.return_value.content = "not valid json {{{"

        findings = scan_service._evaluate_entity(entity, [chunk])

        assert findings == []

    def test_evaluate_entity_filters_low_confidence_findings(self, scan_service, ifc_file):
        """Findings with confidence < CONFIDENCE_THRESHOLD are excluded."""
        from ifc_processor.tests.factories import IFCEntityFactory
        from documents.models import Document, DocumentChunk

        entity = IFCEntityFactory(ifc_file=ifc_file, ifc_type="IfcWall")
        doc = Document.objects.create(
            project=scan_service.project,
            name="Spec.pdf",
            file="spec.pdf",
            status="completed",
        )
        chunk = DocumentChunk.objects.create(
            document=doc, content="Some requirement.", chunk_index=0
        )

        scan_service.llm.invoke.return_value.content = json.dumps(
            {
                "conflicts": [
                    {
                        "property_name": "FireRating",
                        "ifc_value": "EI60",
                        "document_value": "EI120",
                        "title": "Low confidence",
                        "description": "...",
                        "severity": "low",
                        "confidence": 0.3,  # below threshold
                        "suggested_fix": "",
                        "source_chunk_index": 0,
                    }
                ]
            }
        )

        findings = scan_service._evaluate_entity(entity, [chunk])

        assert findings == []


# ── full_scan ──────────────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestFullScan:
    """Tests for full_scan() entry point."""

    def test_full_scan_returns_stats_dict(self, scan_service):
        """full_scan() returns a dict with entities_scanned and conflicts_found keys."""
        # No documents — should return zero stats
        stats = scan_service.full_scan()

        assert "entities_scanned" in stats
        assert "conflicts_found" in stats
        assert stats["entities_scanned"] == 0

    def test_full_scan_creates_scan_run_record(self, scan_service, project):
        """full_scan() creates a ScanRun record."""
        scan_service.full_scan()

        assert ScanRun.objects.filter(project=project, status=ScanRun.Status.COMPLETED).exists()

    def test_full_scan_marks_scan_run_completed(self, scan_service, project):
        """full_scan() marks the ScanRun as COMPLETED on success."""
        scan_service.full_scan()

        run = ScanRun.objects.filter(project=project).latest("created_at")
        assert run.status == ScanRun.Status.COMPLETED
