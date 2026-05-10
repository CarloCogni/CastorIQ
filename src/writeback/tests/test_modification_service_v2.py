# writeback/tests/test_modification_service_v2.py
"""Integration tests for ModificationService.propose() — the V2 pipeline.

The four V2 LLM stages (triage, slot extraction, T3 planner, T3 reviewer)
are mocked at the SERVICE level — the resolver, validators, filter
engine, and proposal-creation code paths run for real against the
fixture entities. These tests catch wiring bugs (mis-passed arguments,
broken dispatch decisions, missing imports) that the per-module unit
tests can't see.

Failures A and B from the production logs are encoded as direct
regression tests so a future change can't silently re-break them.
"""

from unittest.mock import MagicMock, patch

import pytest

from writeback.models import ModificationProposal
from writeback.services.entity_resolver import ResolutionResult
from writeback.services.modification_service import ModificationError, ModificationService
from writeback.services.slot_extractor import SlotResult
from writeback.services.triage_classifier import TriageResult


@pytest.fixture
def mock_guardian():
    with patch("writeback.services.modification_service.GuardianService") as mock_cls:
        instance = mock_cls.return_value
        instance.check.return_value = MagicMock()
        yield instance


@pytest.fixture
def mock_git():
    with patch("writeback.services.modification_service.GitService") as mock_cls:
        yield mock_cls.return_value


def _resolution_for(entities, scope="specific_multi", ifc_type_hint=None):
    return ResolutionResult(
        entities=list(entities),
        ifc_type_hint=ifc_type_hint,
        scope=scope,
        diagnostic=f"{len(entities)} entit{'y' if len(entities) == 1 else 'ies'}",
    )


@pytest.mark.django_db
class TestV2FailureARegression:
    """Failure A from the production logs:
    'Create three new IfcZone entities for Fire Zone A, B, C' →
    used to crash with `IntentParseError: Missing 'tier' in intent`.
    With V2, this routes deterministically to Tier 3.
    """

    def test_create_three_zones_routes_to_tier_3(
        self,
        project,
        ifc_file,
        wall_entities,
        user,
        mock_guardian,
        mock_git,
    ):
        message = "Create three new IfcZone entities for Fire Zone A, Fire Zone B, and Fire Zone C"

        triage_segments = [
            {
                "kind": "CREATE",
                "target_phrase": "three new IfcZone for Fire Zone A, B, C",
                "value_phrase": "IfcZone named Fire Zone A, B, C",
            }
        ]
        slot_payload = SlotResult(
            slots={
                "entity_class": "IfcZone",
                "names": ["Fire Zone A", "Fire Zone B", "Fire Zone C"],
                "parent_phrase": "",
            },
            warnings=[],
        )
        t3_code_payload = {
            "tier": 3,
            "code": (
                "def modify_ifc(model):\n    return {'summary': 'created 3 zones', 'changes': []}\n"
            ),
            "explanation": "Create three IfcZone entities.",
            "confidence": 80,
        }
        review_payload = {"verdict": "ok", "reasons": []}

        empty_resolution = ResolutionResult(
            entities=[], ifc_type_hint=None, scope="empty", diagnostic="no resolution"
        )

        with (
            patch(
                "writeback.services.entity_resolver.EntityNameResolver.resolve",
                return_value=empty_resolution,
            ),
            patch(
                "writeback.services.modification_service.TriageClassifier.classify",
                return_value=TriageResult(segments=triage_segments),
            ),
            patch(
                "writeback.services.modification_service.SlotExtractor.extract",
                return_value=slot_payload,
            ),
            patch(
                "writeback.services.modification_service.Tier3Planner.generate_code",
                return_value=t3_code_payload,
            ),
            patch(
                "writeback.services.modification_service.Tier3Reviewer.review",
                return_value=review_payload,
            ),
        ):
            svc = ModificationService(project)
            proposal = svc.propose(message, user=user)

        assert proposal.tier == 3
        assert proposal.operation == "CODE"
        assert proposal.status == ModificationProposal.Status.PENDING
        assert "modify_ifc" in proposal.intent_json["code"]


@pytest.mark.django_db
class TestV2FailureBRegression:
    """Failure B from the production logs:
    'Create a custom property set Pset_Maintenance on all walls with
    Inspector TBD' → used to be rejected at the resolver step with
    'I couldn't identify any specific entity'. With V2, triage isolates
    'all walls' as the target, the resolver finds them, and routing
    lands in Tier 2 (ADD_PSET).
    """

    def test_pset_on_all_walls_routes_to_tier_2(
        self,
        project,
        ifc_file,
        wall_entities,
        user,
        mock_guardian,
        mock_git,
    ):
        message = (
            "Create a custom property set Pset_Maintenance on all walls "
            'with Inspector "TBD" and LastInspection "2026-01-01"'
        )

        triage_segments = [
            {
                "kind": "PSET",
                "target_phrase": "all walls",
                "value_phrase": (
                    "Pset_Maintenance with Inspector TBD and LastInspection 2026-01-01"
                ),
            }
        ]
        slot_payload = SlotResult(
            slots={
                "operation": "ADD_PSET",
                "pset_name": "Pset_Maintenance",
                "properties": {"Inspector": "TBD", "LastInspection": "2026-01-01"},
            },
            warnings=[],
        )
        # The resolver pins all 5 walls when triage gives it just "all walls".
        all_walls_resolution = _resolution_for(
            wall_entities, scope="all_of_type", ifc_type_hint="IfcWall"
        )

        with patch(
            "writeback.services.entity_resolver.EntityNameResolver.resolve",
            return_value=all_walls_resolution,
        ):
            with (
                patch(
                    "writeback.services.modification_service.TriageClassifier.classify",
                    return_value=TriageResult(segments=triage_segments),
                ),
                patch(
                    "writeback.services.modification_service.SlotExtractor.extract",
                    return_value=slot_payload,
                ),
            ):
                svc = ModificationService(project)
                proposal = svc.propose(message, user=user)

        assert proposal.tier == 2
        assert proposal.operation == "PLAN"
        plan = proposal.intent_json
        assert len(plan["plan"]) == 1
        step = plan["plan"][0]
        assert step["operation"] == "ADD_PSET"
        assert step["params"]["pset_name"] == "Pset_Maintenance"
        # Filter was stamped from the resolver, not the LLM.
        assert "global_ids" in step["filter"]
        assert len(step["filter"]["global_ids"]) == 5


@pytest.mark.django_db
class TestV2Tier1Path:
    """Tier 1 single-segment path: PROPERTY on a standard pset routes T1."""

    def test_property_on_known_pset_creates_tier1_proposal(
        self,
        project,
        ifc_file,
        wall_entities,
        user,
        mock_guardian,
        mock_git,
    ):
        message = "set FireRating to EI120 on all walls"
        triage_segments = [
            {
                "kind": "PROPERTY",
                "target_phrase": "all walls",
                "value_phrase": "FireRating to EI120",
            }
        ]
        slot_payload = SlotResult(
            slots={
                "pset": "Pset_WallCommon",
                "property": "FireRating",
                "value": "EI120",
            },
            warnings=[],
        )
        resolution = _resolution_for(wall_entities, scope="all_of_type", ifc_type_hint="IfcWall")

        with (
            patch(
                "writeback.services.entity_resolver.EntityNameResolver.resolve",
                return_value=resolution,
            ),
            patch(
                "writeback.services.modification_service.TriageClassifier.classify",
                return_value=TriageResult(segments=triage_segments),
            ),
            patch(
                "writeback.services.modification_service.SlotExtractor.extract",
                return_value=slot_payload,
            ),
        ):
            svc = ModificationService(project)
            proposal = svc.propose(message, user=user)

        assert proposal.tier == 1
        assert proposal.operation == "SET_PROPERTY"
        assert proposal.intent_json["pset"] == "Pset_WallCommon"
        assert proposal.intent_json["property"] == "FireRating"
        assert proposal.intent_json["new_value"] == "EI120"
        # Filter is global_ids only (resolver-derived, not LLM-derived).
        assert "global_ids" in proposal.filter_spec
        assert len(proposal.filter_spec["global_ids"]) == 5


@pytest.mark.django_db
class TestV2TolerantPropertyWording:
    """Regression for the production trace where the V2 router rejected
    a previously-working request because the slot extractor returned the
    user's wording verbatim ("fire rating") and the registry lookup was
    case-sensitive. After the fix, ``_maybe_infer_pset`` canonicalizes
    both pset and property against the registry so the Tier1Writer
    receives the IFC4-correct CamelCase form.
    """

    def test_set_fire_rating_of_all_walls_routes_t1(
        self,
        project,
        ifc_file,
        wall_entities,
        user,
        mock_guardian,
        mock_git,
    ):
        message = "Set the fire rating of all walls to EI120"
        triage_segments = [
            {
                "kind": "PROPERTY",
                "target_phrase": "all walls",
                "value_phrase": "fire rating to EI120",
            }
        ]
        # Slot extractor returns the user's wording, NOT the canonical name —
        # this is what currently happens in production and what triggered
        # the regression. The router must canonicalize.
        slot_payload = SlotResult(
            slots={
                "pset": "",
                "property": "fire rating",
                "value": "EI120",
            },
            warnings=[],
        )
        resolution = _resolution_for(wall_entities, scope="all_of_type", ifc_type_hint="IfcWall")

        with (
            patch(
                "writeback.services.entity_resolver.EntityNameResolver.resolve",
                return_value=resolution,
            ),
            patch(
                "writeback.services.modification_service.TriageClassifier.classify",
                return_value=TriageResult(segments=triage_segments),
            ),
            patch(
                "writeback.services.modification_service.SlotExtractor.extract",
                return_value=slot_payload,
            ),
        ):
            svc = ModificationService(project)
            proposal = svc.propose(message, user=user)

        assert proposal.tier == 1
        assert proposal.operation == "SET_PROPERTY"
        # Critical: pset inferred AND property canonicalized to registry form.
        assert proposal.intent_json["pset"] == "Pset_WallCommon"
        assert proposal.intent_json["property"] == "FireRating"
        assert proposal.intent_json["new_value"] == "EI120"


@pytest.mark.django_db
class TestV2Rejections:
    """V2 rejection paths via the deterministic tier router."""

    def test_out_of_scope_segment_rejects_with_reason(
        self,
        project,
        ifc_file,
        wall_entities,
        user,
        mock_guardian,
        mock_git,
    ):
        message = "Move Wall-01 1 metre east"
        triage_segments = [
            {
                "kind": "OUT_OF_SCOPE",
                "target_phrase": "Wall-01",
                "value_phrase": "move 1 metre east",
                "reason": "Geometric edits are out of scope.",
            }
        ]

        with patch(
            "writeback.services.modification_service.TriageClassifier.classify",
            return_value=TriageResult(segments=triage_segments),
        ):
            svc = ModificationService(project)
            with pytest.raises(ModificationError) as exc:
                svc.propose(message, user=user)
        assert "geometric" in str(exc.value).lower()

    def test_unclear_segment_rejects_with_missing_slots(
        self,
        project,
        ifc_file,
        wall_entities,
        user,
        mock_guardian,
        mock_git,
    ):
        message = "do something"
        triage_segments = [
            {
                "kind": "UNCLEAR",
                "target_phrase": "",
                "value_phrase": "",
                "missing": ["target", "value"],
            }
        ]

        with patch(
            "writeback.services.modification_service.TriageClassifier.classify",
            return_value=TriageResult(segments=triage_segments),
        ):
            svc = ModificationService(project)
            with pytest.raises(ModificationError) as exc:
                svc.propose(message, user=user)
        # The router's rejection reason names the missing slots.
        msg = str(exc.value)
        assert "target" in msg
        assert "value" in msg

    def test_property_segment_with_unresolvable_target_rejects(
        self,
        project,
        ifc_file,
        wall_entities,
        user,
        mock_guardian,
        mock_git,
    ):
        """Triage produces a clean PROPERTY segment but the resolver
        returns empty (the target_phrase doesn't match anything in the
        DB). The T1 dispatcher rejects with a clear reason.
        """
        message = "set FireRating to EI120 on the unicorn"
        triage_segments = [
            {
                "kind": "PROPERTY",
                "target_phrase": "the unicorn",
                "value_phrase": "FireRating to EI120",
            }
        ]
        slot_payload = SlotResult(
            slots={"pset": "", "property": "FireRating", "value": "EI120"},
            warnings=[],
        )
        empty_resolution = ResolutionResult(
            entities=[], ifc_type_hint=None, scope="empty", diagnostic="no match"
        )

        with (
            patch(
                "writeback.services.entity_resolver.EntityNameResolver.resolve",
                return_value=empty_resolution,
            ),
            patch(
                "writeback.services.modification_service.TriageClassifier.classify",
                return_value=TriageResult(segments=triage_segments),
            ),
            patch(
                "writeback.services.modification_service.SlotExtractor.extract",
                return_value=slot_payload,
            ),
        ):
            svc = ModificationService(project)
            with pytest.raises(ModificationError) as exc:
                svc.propose(message, user=user)
        assert "no entities matched" in str(exc.value).lower()
