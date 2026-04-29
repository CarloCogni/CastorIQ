# writeback/tests/test_conflict_scan_service.py
"""Tests for ConflictScanService — LLM always mocked, pgvector operations patched."""

import json
from unittest.mock import MagicMock

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
        from documents.models import Document, DocumentChunk
        from ifc_processor.tests.factories import IFCEntityFactory

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

        from documents.models import Document, DocumentChunk
        from ifc_processor.tests.factories import IFCEntityFactory

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

    def test_upsert_skips_when_values_equal(self, scan_service, project, ifc_file):
        """LLM ignored its own rule: ifc_value == document_value must be dropped."""
        from documents.models import Document, DocumentChunk
        from ifc_processor.tests.factories import IFCEntityFactory

        entity = IFCEntityFactory(ifc_file=ifc_file, ifc_type="IfcWall")
        doc = Document.objects.create(
            project=project, name="Spec.pdf", file="spec.pdf", status="completed"
        )
        chunk = DocumentChunk.objects.create(
            document=doc, content="Walls shall have EI60.", chunk_index=0
        )
        scan_run = ScanRun.objects.create(
            project=project,
            triggered_by=scan_service.user,
            scan_type=ScanRun.ScanType.FULL,
            status=ScanRun.Status.RUNNING,
            llm_model_used="test",
        )

        finding = self._make_finding()
        finding["ifc_value"] = "EI60"
        finding["document_value"] = "EI60"

        result = scan_service._upsert_conflict(entity, chunk, finding, scan_run)

        assert result == "skipped"
        assert not Conflict.objects.filter(project=project).exists()

    def test_upsert_skips_when_values_equal_after_normalisation(
        self, scan_service, project, ifc_file
    ):
        """'EI60' vs 'EI 60' (whitespace-only difference) must also be dropped."""
        from documents.models import Document, DocumentChunk
        from ifc_processor.tests.factories import IFCEntityFactory

        entity = IFCEntityFactory(ifc_file=ifc_file, ifc_type="IfcWall")
        doc = Document.objects.create(
            project=project, name="Spec.pdf", file="spec.pdf", status="completed"
        )
        chunk = DocumentChunk.objects.create(
            document=doc, content="Walls shall have EI 60.", chunk_index=0
        )
        scan_run = ScanRun.objects.create(
            project=project,
            triggered_by=scan_service.user,
            scan_type=ScanRun.ScanType.FULL,
            status=ScanRun.Status.RUNNING,
            llm_model_used="test",
        )

        finding = self._make_finding()
        finding["ifc_value"] = "EI60"
        finding["document_value"] = "EI 60"

        result = scan_service._upsert_conflict(entity, chunk, finding, scan_run)

        assert result == "skipped"

    def test_upsert_skips_when_values_equal_case_insensitive(self, scan_service, project, ifc_file):
        """'ei60' vs 'EI60' (case-only difference) must also be dropped."""
        from documents.models import Document, DocumentChunk
        from ifc_processor.tests.factories import IFCEntityFactory

        entity = IFCEntityFactory(ifc_file=ifc_file, ifc_type="IfcWall")
        doc = Document.objects.create(
            project=project, name="Spec.pdf", file="spec.pdf", status="completed"
        )
        chunk = DocumentChunk.objects.create(
            document=doc, content="Walls shall have EI60.", chunk_index=0
        )
        scan_run = ScanRun.objects.create(
            project=project,
            triggered_by=scan_service.user,
            scan_type=ScanRun.ScanType.FULL,
            status=ScanRun.Status.RUNNING,
            llm_model_used="test",
        )

        finding = self._make_finding()
        finding["ifc_value"] = "ei60"
        finding["document_value"] = "EI60"

        result = scan_service._upsert_conflict(entity, chunk, finding, scan_run)

        assert result == "skipped"

    def test_upsert_skips_dismissed_conflict(self, scan_service, project, ifc_file):
        """Dismissed conflicts are never recreated."""
        import hashlib

        from documents.models import Document, DocumentChunk
        from ifc_processor.tests.factories import IFCEntityFactory

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

    def test_upsert_coerces_null_ifc_value_to_fallback(self, scan_service, project, ifc_file):
        """LLM returning ``"ifc_value": null`` is coerced to a fallback string.

        Regression for the NotNullViolation raised when the roof/thermal-transmittance
        scan produced a finding with a null ifc_value — the DB column is NOT NULL.
        """
        from documents.models import Document, DocumentChunk
        from ifc_processor.tests.factories import IFCEntityFactory

        entity = IFCEntityFactory(ifc_file=ifc_file, ifc_type="IfcRoof")
        doc = Document.objects.create(
            project=project, name="Spec.pdf", file="spec.pdf", status="completed"
        )
        chunk = DocumentChunk.objects.create(
            document=doc, content="Roof shall have U <= 0.2.", chunk_index=0
        )
        scan_run = ScanRun.objects.create(
            project=project,
            triggered_by=scan_service.user,
            scan_type=ScanRun.ScanType.FULL,
            status=ScanRun.Status.RUNNING,
            llm_model_used="test",
        )

        finding = self._make_finding()
        finding["ifc_value"] = None

        result = scan_service._upsert_conflict(entity, chunk, finding, scan_run)

        assert result == "created"
        conflict = Conflict.objects.get(project=project)
        assert conflict.ifc_value == "(not set)"

    def test_upsert_tolerates_null_property_name(self, scan_service, project, ifc_file):
        """``"property_name": null`` must not crash on the slice / hash path."""
        from documents.models import Document, DocumentChunk
        from ifc_processor.tests.factories import IFCEntityFactory

        entity = IFCEntityFactory(ifc_file=ifc_file, ifc_type="IfcWall")
        doc = Document.objects.create(
            project=project, name="Spec.pdf", file="spec.pdf", status="completed"
        )
        chunk = DocumentChunk.objects.create(
            document=doc, content="Walls shall be labeled.", chunk_index=0
        )
        scan_run = ScanRun.objects.create(
            project=project,
            triggered_by=scan_service.user,
            scan_type=ScanRun.ScanType.FULL,
            status=ScanRun.Status.RUNNING,
            llm_model_used="test",
        )

        finding = self._make_finding()
        finding["property_name"] = None

        result = scan_service._upsert_conflict(entity, chunk, finding, scan_run)

        assert result == "created"
        conflict = Conflict.objects.get(project=project)
        assert conflict.property_name == ""


# ── _evaluate_entity ──────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestEvaluateEntity:
    """Tests for _evaluate_entity() — LLM mocked."""

    def test_evaluate_entity_returns_findings(self, scan_service, ifc_file):
        """Valid LLM response returns list of conflict dicts."""
        from documents.models import Document, DocumentChunk
        from ifc_processor.tests.factories import IFCEntityFactory

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
        from documents.models import Document, DocumentChunk
        from ifc_processor.tests.factories import IFCEntityFactory

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
        from documents.models import Document, DocumentChunk
        from ifc_processor.tests.factories import IFCEntityFactory

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

    def test_evaluate_entity_drops_wall_requirement_on_beam(self, scan_service, ifc_file):
        """A finding with applies_to_element='wall' is dropped when the entity is IfcBeam.

        Regression for the 'Missing fire rating for external walls' card wrongly
        attached to an IfcBeam (girder) — the scanner would previously persist
        such cross-type findings because the prompt didn't enforce element matching.
        """
        from documents.models import Document, DocumentChunk
        from ifc_processor.tests.factories import IFCEntityFactory

        entity = IFCEntityFactory(ifc_file=ifc_file, ifc_type="IfcBeam", name="girder")
        doc = Document.objects.create(
            project=scan_service.project,
            name="safety.pdf",
            file="safety.pdf",
            status="completed",
        )
        chunk = DocumentChunk.objects.create(
            document=doc, content="External walls shall have EI60.", chunk_index=0
        )

        scan_service.llm.invoke.return_value.content = json.dumps(
            {
                "conflicts": [
                    {
                        "property_name": "FireRating",
                        "ifc_value": "absent (no property sets)",
                        "document_value": "EI60",
                        "applies_to_element": "wall",
                        "title": "Missing fire rating for external walls",
                        "description": "Document requires EI60 for external walls.",
                        "severity": "critical",
                        "confidence": 0.95,
                        "suggested_fix": "Add FireRating = EI60",
                        "source_chunk_index": 0,
                    }
                ]
            }
        )

        findings = scan_service._evaluate_entity(entity, [chunk])

        assert findings == []

    def test_evaluate_entity_keeps_matching_element_type(self, scan_service, ifc_file):
        """applies_to_element='wall' on an IfcWall is kept."""
        from documents.models import Document, DocumentChunk
        from ifc_processor.tests.factories import IFCEntityFactory

        entity = IFCEntityFactory(ifc_file=ifc_file, ifc_type="IfcWallStandardCase")
        doc = Document.objects.create(
            project=scan_service.project,
            name="safety.pdf",
            file="safety.pdf",
            status="completed",
        )
        chunk = DocumentChunk.objects.create(
            document=doc, content="External walls shall have EI60.", chunk_index=0
        )

        scan_service.llm.invoke.return_value.content = json.dumps(
            {
                "conflicts": [
                    {
                        "property_name": "FireRating",
                        "ifc_value": "absent (no property sets)",
                        "document_value": "EI60",
                        "applies_to_element": "wall",
                        "title": "Missing fire rating for external walls",
                        "description": "Document requires EI60.",
                        "severity": "critical",
                        "confidence": 0.95,
                        "suggested_fix": "Add FireRating = EI60",
                        "source_chunk_index": 0,
                    }
                ]
            }
        )

        findings = scan_service._evaluate_entity(entity, [chunk])

        assert len(findings) == 1
        assert findings[0]["applies_to_element"] == "wall"

    def test_evaluate_entity_keeps_any_label_on_any_type(self, scan_service, ifc_file):
        """applies_to_element='any' bypasses the type gate."""
        from documents.models import Document, DocumentChunk
        from ifc_processor.tests.factories import IFCEntityFactory

        entity = IFCEntityFactory(ifc_file=ifc_file, ifc_type="IfcBeam")
        doc = Document.objects.create(
            project=scan_service.project,
            name="spec.pdf",
            file="spec.pdf",
            status="completed",
        )
        chunk = DocumentChunk.objects.create(
            document=doc, content="All elements shall carry a label.", chunk_index=0
        )

        scan_service.llm.invoke.return_value.content = json.dumps(
            {
                "conflicts": [
                    {
                        "property_name": "Label",
                        "ifc_value": "absent",
                        "document_value": "required",
                        "applies_to_element": "any",
                        "title": "Missing label",
                        "description": "...",
                        "severity": "low",
                        "confidence": 0.85,
                        "suggested_fix": "Add Label",
                        "source_chunk_index": 0,
                    }
                ]
            }
        )

        findings = scan_service._evaluate_entity(entity, [chunk])

        assert len(findings) == 1

    def test_evaluate_entity_unknown_label_is_fail_open(self, scan_service, ifc_file):
        """An applies_to_element label not in ELEMENT_TYPE_MAP is allowed through (fail-open)."""
        from documents.models import Document, DocumentChunk
        from ifc_processor.tests.factories import IFCEntityFactory

        entity = IFCEntityFactory(ifc_file=ifc_file, ifc_type="IfcWall")
        doc = Document.objects.create(
            project=scan_service.project,
            name="spec.pdf",
            file="spec.pdf",
            status="completed",
        )
        chunk = DocumentChunk.objects.create(
            document=doc, content="Partitions shall resist fire.", chunk_index=0
        )

        scan_service.llm.invoke.return_value.content = json.dumps(
            {
                "conflicts": [
                    {
                        "property_name": "FireRating",
                        "ifc_value": "EI60",
                        "document_value": "EI90",
                        "applies_to_element": "partition",  # not in map
                        "title": "Fire rating too low",
                        "description": "...",
                        "severity": "high",
                        "confidence": 0.8,
                        "suggested_fix": "Raise FireRating",
                        "source_chunk_index": 0,
                    }
                ]
            }
        )

        findings = scan_service._evaluate_entity(entity, [chunk])

        assert len(findings) == 1


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


# ── Per-entity heartbeat ──────────────────────────────────────────────────────


@pytest.mark.django_db
class TestPerEntityHeartbeat:
    """The compare loop must emit running + done per entity so the live UI has
    a real heartbeat — without this the user can't tell whether the scan is
    progressing or stuck on the current entity."""

    def test_loop_emits_running_and_done_per_entity_with_label_detail(
        self, scan_service, ifc_file, monkeypatch
    ):
        from documents.models import Document, DocumentChunk
        from ifc_processor.tests.factories import IFCEntityFactory
        from writeback.services.emitters import CapturingEmitter

        e1 = IFCEntityFactory(ifc_file=ifc_file, ifc_type="IfcWall", name="Wall A")
        e2 = IFCEntityFactory(ifc_file=ifc_file, ifc_type="IfcWall", name="Wall B")
        doc = Document.objects.create(
            project=scan_service.project,
            name="Spec.pdf",
            file="spec.pdf",
            status="completed",
        )
        chunk = DocumentChunk.objects.create(
            document=doc, content="Walls shall have EI120.", chunk_index=0
        )

        # Bypass vector search — feed the loop a known entity → chunks map.
        monkeypatch.setattr(scan_service, "_get_requirement_chunks", lambda: [chunk])
        monkeypatch.setattr(
            scan_service,
            "_build_entity_chunk_map",
            lambda req_chunks: {e1: [chunk], e2: [chunk]},
        )
        scan_service.llm.invoke.return_value.content = '{"conflicts": []}'

        emitter = CapturingEmitter()
        scan_service.full_scan(emitter=emitter)

        compare = [ev for ev in emitter.events if ev["phase"] == "compare"]
        running = [ev for ev in compare if ev["status"] == "running"]
        done = [ev for ev in compare if ev["status"] == "done"]

        assert len(running) == 2
        assert len(done) == 2

        for ev in running + done:
            detail = ev["detail"]
            assert detail["ifc_type"] == "IfcWall"
            assert detail["entity_name"] in {"Wall A", "Wall B"}
            assert detail["total"] == 2
            assert detail["current"] in {1, 2}

    def test_loop_raises_cancellation_error_before_calling_llm_when_flag_set(
        self, scan_service, ifc_file, monkeypatch
    ):
        """If the cancel flag is set between entities, the loop short-circuits
        before the next LLM call rather than spending another N seconds on Ollama."""
        from documents.models import Document, DocumentChunk
        from ifc_processor.tests.factories import IFCEntityFactory
        from writeback.services.emitters import CancellationError, CapturingEmitter

        e1 = IFCEntityFactory(ifc_file=ifc_file, ifc_type="IfcWall", name="Wall A")
        e2 = IFCEntityFactory(ifc_file=ifc_file, ifc_type="IfcWall", name="Wall B")
        doc = Document.objects.create(
            project=scan_service.project,
            name="Spec.pdf",
            file="spec.pdf",
            status="completed",
        )
        chunk = DocumentChunk.objects.create(
            document=doc, content="Walls shall have EI120.", chunk_index=0
        )

        monkeypatch.setattr(scan_service, "_get_requirement_chunks", lambda: [chunk])
        monkeypatch.setattr(
            scan_service,
            "_build_entity_chunk_map",
            lambda req_chunks: {e1: [chunk], e2: [chunk]},
        )

        emitter = CapturingEmitter()
        llm_calls = {"n": 0}

        def fake_evaluate(entity, chunks):
            llm_calls["n"] += 1
            # Simulate the user clicking Cancel during the first entity's LLM call.
            emitter.request_cancel()
            return []

        monkeypatch.setattr(scan_service, "_evaluate_entity", fake_evaluate)

        with pytest.raises(CancellationError):
            scan_service.full_scan(emitter=emitter)

        # First entity's LLM call ran (the cancel happened during it).
        # Second entity must NOT have triggered _evaluate_entity — the explicit
        # pre-LLM check should have raised before it.
        assert llm_calls["n"] == 1

    def test_loop_emits_error_event_when_evaluate_entity_raises(
        self, scan_service, ifc_file, monkeypatch
    ):
        """A per-entity LLM failure produces a compare/error event AND the loop
        continues to the next entity (the broad except in the loop swallows
        non-CancellationError exceptions)."""
        from documents.models import Document, DocumentChunk
        from ifc_processor.tests.factories import IFCEntityFactory
        from writeback.services.emitters import CapturingEmitter

        e1 = IFCEntityFactory(ifc_file=ifc_file, ifc_type="IfcWall", name="Wall A")
        e2 = IFCEntityFactory(ifc_file=ifc_file, ifc_type="IfcWall", name="Wall B")
        doc = Document.objects.create(
            project=scan_service.project,
            name="Spec.pdf",
            file="spec.pdf",
            status="completed",
        )
        chunk = DocumentChunk.objects.create(
            document=doc, content="Walls shall have EI120.", chunk_index=0
        )

        monkeypatch.setattr(scan_service, "_get_requirement_chunks", lambda: [chunk])
        monkeypatch.setattr(
            scan_service,
            "_build_entity_chunk_map",
            lambda req_chunks: {e1: [chunk], e2: [chunk]},
        )

        calls = {"n": 0}

        def fake_evaluate(entity, chunks):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("ollama timed out")
            return []

        monkeypatch.setattr(scan_service, "_evaluate_entity", fake_evaluate)

        emitter = CapturingEmitter()
        scan_service.full_scan(emitter=emitter)

        compare = [ev for ev in emitter.events if ev["phase"] == "compare"]
        statuses = [ev["status"] for ev in compare]

        assert statuses.count("running") == 2
        assert statuses.count("error") == 1
        assert statuses.count("done") == 1
