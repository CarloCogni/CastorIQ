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
from writeback.services.proposal_pipeline import ProposalPipeline
from writeback.services.slot_extractor import SlotResult
from writeback.services.triage_classifier import TriageResult


@pytest.fixture
def mock_guardian():
    with patch("writeback.services.proposal_service.GuardianService") as mock_cls:
        instance = mock_cls.return_value
        instance.check.return_value = MagicMock()
        yield instance


@pytest.fixture
def mock_git():
    with patch("writeback.services.execution_service.GitService") as mock_cls:
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
        # T3OpPlanner is patched to emit typed CREATE_ENTITY ops, which is
        # what a real planner does for this request now — the original bug
        # was the crash before routing, not the path taken afterwards.
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

        from writeback.services.t3_op_planner import T3OpPlanResult

        op_plan = T3OpPlanResult(
            ops=tuple(
                {"op": "CREATE_ENTITY", "ifc_class": "IfcZone", "name": name}
                for name in ("Fire Zone A", "Fire Zone B", "Fire Zone C")
            ),
            explanation="Create three zones.",
            confidence=90,
        )

        empty_resolution = ResolutionResult(
            entities=[], ifc_type_hint=None, scope="empty", diagnostic="no resolution"
        )

        with (
            patch(
                "writeback.services.entity_resolver.EntityNameResolver.resolve",
                return_value=empty_resolution,
            ),
            patch(
                "writeback.services.proposal_pipeline.TriageClassifier.classify",
                return_value=TriageResult(segments=triage_segments),
            ),
            patch(
                "writeback.services.proposal_pipeline.SlotExtractor.extract",
                return_value=slot_payload,
            ),
            patch(
                "writeback.services.proposal_pipeline.T3OpPlanner.plan",
                return_value=op_plan,
            ),
            patch(
                "writeback.services.proposal_pipeline.Tier3Planner.generate_code",
                return_value=t3_code_payload,
            ),
            patch(
                "writeback.services.proposal_pipeline.Tier3Reviewer.review",
                return_value=review_payload,
            ),
        ):
            svc = ModificationService(project)
            proposal = svc.propose(message, user=user)

        assert proposal.tier == 3
        assert proposal.operation == "OPS"
        assert proposal.status == ModificationProposal.Status.PENDING
        assert proposal.affected_count == 3


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
                    "writeback.services.proposal_pipeline.TriageClassifier.classify",
                    return_value=TriageResult(segments=triage_segments),
                ),
                patch(
                    "writeback.services.proposal_pipeline.SlotExtractor.extract",
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

    def test_t2_persists_a_journal_proposal(
        self,
        project,
        ifc_file,
        wall_entities,
        user,
        mock_guardian,
        mock_git,
        settings,
    ):
        """An ADD_PSET request persists a
        source_tier=2 MutationJournal (one mutation per entity×new-property)
        and a journal-derived per-property diff preview."""
        import json

        from ifc_processor.services.journal import MutationJournal

        message = (
            "add Pset_Maintenance to all walls with Inspector TBD and LastInspection 2026-01-01"
        )
        triage_segments = [
            {"kind": "PSET", "target_phrase": "all walls", "value_phrase": "Pset_Maintenance ..."}
        ]
        slot_payload = SlotResult(
            slots={
                "operation": "ADD_PSET",
                "pset_name": "Pset_Maintenance",
                "properties": {"Inspector": "TBD", "LastInspection": "2026-01-01"},
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
                "writeback.services.proposal_pipeline.TriageClassifier.classify",
                return_value=TriageResult(segments=triage_segments),
            ),
            patch(
                "writeback.services.proposal_pipeline.SlotExtractor.extract",
                return_value=slot_payload,
            ),
        ):
            svc = ModificationService(project)
            proposal = svc.propose(message, user=user)

        assert proposal.tier == 2
        journal = MutationJournal.from_json_dict(proposal.changes)
        assert journal.source_tier == 2
        # 5 walls × 2 fresh properties (neither is on Pset_WallCommon) = 10.
        assert len(journal.mutations) == 10
        assert {m.op.value for m in journal.mutations} == {"ADD_PSET"}
        assert journal.base_fingerprint
        rows = json.loads(proposal.diff_preview)
        assert len(rows) == 10  # per-property, not collapsed to "N properties"
        assert rows[0]["field"] == "Pset_Maintenance.Inspector"
        assert rows[0]["new_value"] == "TBD"


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
                "writeback.services.proposal_pipeline.TriageClassifier.classify",
                return_value=TriageResult(segments=triage_segments),
            ),
            patch(
                "writeback.services.proposal_pipeline.SlotExtractor.extract",
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

    def test_t1_persists_a_journal_proposal(
        self,
        project,
        ifc_file,
        wall_entities,
        user,
        mock_guardian,
        mock_git,
        settings,
    ):
        """The request persists a
        MutationJournal in ``changes`` and a journal-derived diff preview."""
        import json

        from ifc_processor.services.journal import MutationJournal

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
                "writeback.services.proposal_pipeline.TriageClassifier.classify",
                return_value=TriageResult(segments=triage_segments),
            ),
            patch(
                "writeback.services.proposal_pipeline.SlotExtractor.extract",
                return_value=slot_payload,
            ),
        ):
            svc = ModificationService(project)
            proposal = svc.propose(message, user=user)

        assert proposal.tier == 1
        # changes carries a decodable journal, not a bare intent dict.
        journal = MutationJournal.from_json_dict(proposal.changes)
        assert journal.source_tier == 1
        assert len(journal.mutations) == 5
        assert journal.base_fingerprint
        assert all(m.old_value == "EI60" for m in journal.mutations)
        assert all(m.new_value == "EI120" for m in journal.mutations)
        # Diff preview is journal-derived with the same row shape the UI reads.
        rows = json.loads(proposal.diff_preview)
        assert len(rows) == 5
        assert rows[0]["field"] == "Pset_WallCommon.FireRating"
        assert rows[0]["old_value"] == "EI60"
        assert rows[0]["new_value"] == "EI120"
        # Provenance stays on intent_json for audit / failure analysis.
        assert proposal.intent_json["operation"] == "SET_PROPERTY"


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
                "writeback.services.proposal_pipeline.TriageClassifier.classify",
                return_value=TriageResult(segments=triage_segments),
            ),
            patch(
                "writeback.services.proposal_pipeline.SlotExtractor.extract",
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
            "writeback.services.proposal_pipeline.TriageClassifier.classify",
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
            "writeback.services.proposal_pipeline.TriageClassifier.classify",
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
                "writeback.services.proposal_pipeline.TriageClassifier.classify",
                return_value=TriageResult(segments=triage_segments),
            ),
            patch(
                "writeback.services.proposal_pipeline.SlotExtractor.extract",
                return_value=slot_payload,
            ),
        ):
            svc = ModificationService(project)
            with pytest.raises(ModificationError) as exc:
                svc.propose(message, user=user)
        assert "no entities matched" in str(exc.value).lower()


class TestRetryOf:
    """The retry_of flow: a prior FailureRecord's structured boundary errors
    are decoded and fed back into the stage that failed."""

    def test_retry_prior_errors_decodes_stored_record(self):
        from types import SimpleNamespace

        record = SimpleNamespace(
            intent_json={
                "stage": "slots:property",
                "boundary_errors": [
                    {"code": "MISSING_FIELD", "path": "value", "hint": "provide a value"}
                ],
            }
        )
        stage, errors = ProposalPipeline._retry_prior_errors(record)
        assert stage == "slots:property"
        assert len(errors) == 1
        assert errors[0].code == "MISSING_FIELD"
        assert errors[0].path == "value"

    def test_retry_prior_errors_handles_none_and_empty(self):
        from types import SimpleNamespace

        assert ProposalPipeline._retry_prior_errors(None) == (None, ())
        empty = SimpleNamespace(intent_json={})
        assert ProposalPipeline._retry_prior_errors(empty) == (None, ())

    @pytest.mark.django_db
    def test_retry_feeds_prior_errors_into_the_failed_stage(
        self, project, ifc_file, wall_entities, user, mock_guardian, mock_git
    ):
        from types import SimpleNamespace

        captured: dict = {}

        def fake_classify(user_message, prior_errors=()):
            captured["prior_errors"] = prior_errors
            return TriageResult(
                segments=[
                    {
                        "kind": "PROPERTY",
                        "target_phrase": "all walls",
                        "value_phrase": "FireRating to EI120",
                    }
                ]
            )

        record = SimpleNamespace(
            intent_json={
                "stage": "triage",
                "boundary_errors": [
                    {"code": "BAD_ENUM", "path": "segments[0].kind", "hint": "bad kind"}
                ],
            }
        )
        slot_payload = SlotResult(
            slots={"pset": "Pset_WallCommon", "property": "FireRating", "value": "EI120"},
            warnings=[],
        )
        resolution = _resolution_for(wall_entities, scope="all_of_type", ifc_type_hint="IfcWall")

        with (
            patch(
                "writeback.services.entity_resolver.EntityNameResolver.resolve",
                return_value=resolution,
            ),
            patch(
                "writeback.services.proposal_pipeline.TriageClassifier.classify",
                side_effect=fake_classify,
            ),
            patch(
                "writeback.services.proposal_pipeline.SlotExtractor.extract",
                return_value=slot_payload,
            ),
        ):
            svc = ModificationService(project)
            svc.propose("set FireRating to EI120 on all walls", user=user, retry_of=record)

        assert len(captured["prior_errors"]) == 1
        assert captured["prior_errors"][0].code == "BAD_ENUM"


@pytest.mark.django_db
class TestV2Tier3TypedOps:
    """CP8: T3 tries typed ops first, falls back to generated code."""

    def _segments(self, kind="CREATE"):
        return [
            {
                "kind": kind,
                "target_phrase": "a new zone",
                "value_phrase": "IfcZone named Fire Zone A",
            }
        ]

    def _slots(self, slots=None):
        return SlotResult(
            slots=slots
            or {"entity_class": "IfcZone", "names": ["Fire Zone A"], "parent_phrase": ""},
            warnings=[],
        )

    def _run(
        self,
        project,
        user,
        *,
        op_plan=None,
        code_payload=None,
        segments=None,
        resolution=None,
        slots=None,
    ):
        from writeback.services.t3_op_planner import T3OpPlanResult

        # CREATE resolves to nothing by design (NEW_TARGET); DELETE and
        # RELATIONSHIP must resolve real targets or the router rejects at tier 0.
        empty_resolution = ResolutionResult(
            entities=[], ifc_type_hint=None, scope="empty", diagnostic="no resolution"
        )
        patches = [
            patch(
                "writeback.services.entity_resolver.EntityNameResolver.resolve",
                return_value=resolution if resolution is not None else empty_resolution,
            ),
            patch(
                "writeback.services.proposal_pipeline.TriageClassifier.classify",
                return_value=TriageResult(segments=segments or self._segments()),
            ),
            patch(
                "writeback.services.proposal_pipeline.SlotExtractor.extract",
                return_value=self._slots(slots),
            ),
            patch(
                "writeback.services.proposal_pipeline.T3OpPlanner.plan",
                return_value=op_plan or T3OpPlanResult(cannot_express=True, reason="nope"),
            ),
            patch(
                "writeback.services.proposal_pipeline.Tier3Planner.generate_code",
                return_value=code_payload
                or {
                    "tier": 3,
                    "code": "def modify_ifc(model):\n    return {'summary': 's', 'changes': []}",
                    "explanation": "generated",
                    "confidence": 80,
                },
            ),
            patch(
                "writeback.services.proposal_pipeline.Tier3Reviewer.review",
                return_value={"verdict": "ALIGNED", "summary": "ok", "steps": [{}]},
            ),
        ]
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            return ModificationService(project).propose("create a zone", user=user)

    def test_typed_ops_produce_an_ops_proposal_with_a_diff(
        self, project, ifc_file, wall_entities, user, mock_guardian, mock_git, settings
    ):
        import json

        from ifc_processor.services.journal import MutationJournal
        from writeback.services.t3_op_planner import T3OpPlanResult

        plan = T3OpPlanResult(
            ops=({"op": "CREATE_ENTITY", "ifc_class": "IfcZone", "name": "Fire Zone A"},),
            explanation="Create one zone.",
            confidence=92,
        )

        proposal = self._run(project, user, op_plan=plan)

        assert proposal.tier == 3
        assert proposal.operation == "OPS"
        journal = MutationJournal.from_json_dict(proposal.changes)
        assert journal.source_tier == 3
        assert len(journal.mutations) == 1
        # T3 finally has a diff preview and a real affected count.
        rows = json.loads(proposal.diff_preview)
        assert len(rows) == 1
        assert rows[0]["new_value"].startswith("create IfcZone")
        assert proposal.affected_count == 1
        # And crucially, no code to review.
        assert "code" not in proposal.intent_json

    def test_cannot_express_falls_back_to_code_with_a_reason(
        self, project, ifc_file, wall_entities, user, mock_guardian, mock_git, settings
    ):
        from writeback.services.t3_op_planner import T3OpPlanResult

        plan = T3OpPlanResult(
            cannot_express=True, reason="Moving between storeys is not available."
        )

        proposal = self._run(project, user, op_plan=plan)

        assert proposal.operation == "CODE"
        assert "code" in proposal.intent_json
        # The human is told WHY they're reading Python.
        assert "storeys" in proposal.explanation

    def test_relationship_uses_typed_ops(
        self, project, ifc_file, wall_entities, user, mock_guardian, mock_git, settings
    ):
        """A container move is a typed op now — no code, no review checkbox."""
        import json

        from writeback.services.t3_op_planner import T3OpPlanResult

        # The resolver is patched once for every call, so the same entity
        # stands in as both the moved element and the destination; the
        # pipeline only needs each resolution to be non-empty.
        wall = wall_entities[0]
        plan = T3OpPlanResult(
            ops=(
                {
                    "op": "ASSIGN_RELATIONSHIP",
                    "global_id": wall.global_id,
                    "destination_global_id": wall_entities[1].global_id,
                    "relation": "container",
                },
            ),
            explanation="Move one wall.",
            confidence=88,
        )

        proposal = self._run(
            project,
            user,
            segments=self._segments(kind="RELATIONSHIP"),
            resolution=_resolution_for(wall_entities),
            op_plan=plan,
            slots={"destination_phrase": "Level 2", "relation": "container"},
        )

        assert proposal.operation == "OPS"
        assert "code" not in proposal.intent_json
        rows = json.loads(proposal.diff_preview)
        assert rows[0]["field"] == "Spatial container"

    def test_relationship_cannot_express_still_falls_back_to_code(
        self, project, ifc_file, wall_entities, user, mock_guardian, mock_git, settings
    ):
        """Group/aggregate moves aren't typed, so the code path must survive."""
        from writeback.services.t3_op_planner import T3OpPlanResult

        proposal = self._run(
            project,
            user,
            segments=self._segments(kind="RELATIONSHIP"),
            resolution=_resolution_for(wall_entities),
            op_plan=T3OpPlanResult(
                cannot_express=True, reason="Group membership is not a typed operation."
            ),
            slots={"destination_phrase": "Zone A", "relation": "container"},
        )

        assert proposal.operation == "CODE"

    def test_code_fallback_is_still_wrapped_in_a_journal(
        self, project, ifc_file, wall_entities, user, mock_guardian, mock_git
    ):
        """Generated code executes through the journal like everything else.

        There is no non-journal execution path left, so a CODE proposal that
        skipped the wrap would be un-executable. ``intent_json`` keeps the raw
        payload the reviewer panel and ack gate read.
        """
        from ifc_processor.services.journal import MutationJournal, MutationOp

        proposal = self._run(project, user)

        assert proposal.operation == "CODE"
        assert proposal.changes != proposal.intent_json
        journal = MutationJournal.from_json_dict(proposal.changes)
        assert [m.op for m in journal.mutations] == [MutationOp.RUN_CODE]
        assert "modify_ifc" in proposal.intent_json["code"]
