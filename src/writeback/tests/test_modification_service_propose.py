# writeback/tests/test_modification_service_propose.py
"""Tests for ModificationService.propose() — LLM always mocked."""

import json
from unittest.mock import MagicMock, patch

import pytest

from writeback.models import ModificationProposal
from writeback.services.emitters import CapturingEmitter
from writeback.services.modification_service import ModificationError, ModificationService


def _make_tier1_intent(
    operation: str = "SET_PROPERTY",
    ifc_type: str = "IfcWall",
    pset: str = "Pset_WallCommon",
    prop: str = "FireRating",
    new_value: str = "EI120",
    confidence: int = 90,
) -> dict:
    """Build a minimal valid Tier 1 intent dict."""
    return {
        "tier": 1,
        "operation": operation,
        "filter": {"ifc_type": ifc_type},
        "pset": pset,
        "property": prop,
        "new_value": new_value,
        "confidence": confidence,
        "explanation": f"Set {pset}.{prop} to {new_value} on {ifc_type}",
    }


@pytest.fixture
def mock_get_llm():
    """Patches get_llm globally in the modification service module."""
    mock_llm = MagicMock()
    with patch("writeback.services.intent_classifier.get_llm", return_value=mock_llm):
        with patch("writeback.services.tier2_planner.get_llm", return_value=mock_llm):
            with patch("writeback.services.tier3_planner.get_llm", return_value=mock_llm):
                with patch("writeback.services.tier3_reviewer.get_llm", return_value=mock_llm):
                    with patch(
                        "writeback.services.feasibility_checker.get_llm",
                        return_value=mock_llm,
                    ):
                        yield mock_llm


@pytest.fixture
def mock_guardian():
    """Patches GuardianService.check to be a no-op."""
    with patch("writeback.services.modification_service.GuardianService") as mock_guardian_cls:
        instance = mock_guardian_cls.return_value
        instance.check.return_value = MagicMock()
        yield instance


@pytest.fixture
def mock_git():
    """Patches GitService so no filesystem operations are attempted."""
    with patch("writeback.services.modification_service.GitService") as mock_git_cls:
        yield mock_git_cls.return_value


@pytest.mark.django_db
class TestProposeHappyPath:
    """Happy-path tests for propose()."""

    def test_propose_tier1_creates_pending_proposal(
        self, project, ifc_file, wall_entities, user, mock_get_llm, mock_guardian, mock_git
    ):
        """Tier 1 intent creates a ModificationProposal with status=PENDING."""
        intent = _make_tier1_intent()
        response = MagicMock()
        response.content = json.dumps(intent)
        mock_get_llm.invoke.return_value = response

        with patch(
            "writeback.services.intent_classifier.IntentClassifier.classify",
            return_value=intent,
        ):
            with patch(
                "core.llm.resolve_model_name",
                return_value="llama3.1:8b",
            ):
                svc = ModificationService(project, user=user)
                proposal = svc.propose(
                    "Set fire rating to EI120 on all walls",
                    user=user,
                    ifc_file=ifc_file,
                )

        assert isinstance(proposal, ModificationProposal)
        assert proposal.status == ModificationProposal.Status.PENDING

    def test_propose_tier1_sets_tier_on_proposal(
        self, project, ifc_file, wall_entities, user, mock_get_llm, mock_guardian, mock_git
    ):
        """Proposal created from Tier 1 intent has tier=1."""
        intent = _make_tier1_intent()

        with patch(
            "writeback.services.intent_classifier.IntentClassifier.classify",
            return_value=intent,
        ):
            with patch("core.llm.resolve_model_name", return_value="llama3.1:8b"):
                svc = ModificationService(project, user=user)
                proposal = svc.propose(
                    "Set fire rating to EI120",
                    user=user,
                    ifc_file=ifc_file,
                )

        assert proposal.tier == 1

    def test_propose_captures_emitter_phases_in_order(
        self, project, ifc_file, wall_entities, user, mock_get_llm, mock_guardian, mock_git
    ):
        """CapturingEmitter records feasibility, classify, validate, diff, and guardian phases."""
        intent = _make_tier1_intent()
        emitter = CapturingEmitter()

        with patch(
            "writeback.services.intent_classifier.IntentClassifier.classify",
            return_value=intent,
        ):
            with patch("core.llm.resolve_model_name", return_value="llama3.1:8b"):
                svc = ModificationService(project, user=user)
                svc.propose(
                    "Set fire rating to EI120 on all walls",
                    user=user,
                    ifc_file=ifc_file,
                    emitter=emitter,
                )

        phases = [e["phase"] for e in emitter.events]
        assert "feasibility" in phases
        assert "classify" in phases
        assert "validate" in phases
        assert "diff" in phases
        assert "guardian" in phases

    def test_propose_with_null_emitter_does_not_raise(
        self, project, ifc_file, wall_entities, user, mock_get_llm, mock_guardian, mock_git
    ):
        """propose() with no emitter argument uses NullEmitter (no exception)."""
        intent = _make_tier1_intent()

        with patch(
            "writeback.services.intent_classifier.IntentClassifier.classify",
            return_value=intent,
        ):
            with patch("core.llm.resolve_model_name", return_value="llama3.1:8b"):
                svc = ModificationService(project, user=user)
                proposal = svc.propose(
                    "Set fire rating to EI120",
                    user=user,
                    ifc_file=ifc_file,
                )

        assert proposal is not None


@pytest.mark.django_db
class TestProposeFailureModes:
    """Failure mode tests for propose()."""

    def test_propose_no_entities_raises_modification_error(
        self, project, user, mock_get_llm, mock_guardian, mock_git
    ):
        """Empty project (no IFC entities) raises ModificationError immediately."""
        with patch("core.llm.resolve_model_name", return_value="llama3.1:8b"):
            svc = ModificationService(project, user=user)

            with pytest.raises(ModificationError, match="No processed IFC entities"):
                svc.propose("Set fire rating to EI120", user=user)

    def test_propose_intent_parse_error_raises_modification_error(
        self, project, ifc_file, wall_entities, user, mock_get_llm, mock_guardian, mock_git
    ):
        """IntentParseError from classifier is re-raised as ModificationError."""
        from writeback.services.intent_classifier import IntentParseError

        with patch(
            "writeback.services.intent_classifier.IntentClassifier.classify",
            side_effect=IntentParseError("Unparseable"),
        ):
            with patch("core.llm.resolve_model_name", return_value="llama3.1:8b"):
                svc = ModificationService(project, user=user)

                with pytest.raises(ModificationError, match="Could not understand"):
                    svc.propose("xyzzy gibberish", user=user, ifc_file=ifc_file)

    def test_propose_low_confidence_raises_modification_error(
        self, project, ifc_file, wall_entities, user, mock_get_llm, mock_guardian, mock_git
    ):
        """Intent with confidence < 60 raises ModificationError."""
        intent = _make_tier1_intent(confidence=30)

        with patch(
            "writeback.services.intent_classifier.IntentClassifier.classify",
            return_value=intent,
        ):
            with patch("core.llm.resolve_model_name", return_value="llama3.1:8b"):
                svc = ModificationService(project, user=user)

                with pytest.raises(ModificationError, match="[Ll]ow confidence"):
                    svc.propose(
                        "Do something vague",
                        user=user,
                        ifc_file=ifc_file,
                    )

    def test_propose_feasibility_rejected_raises_modification_error(
        self, project, ifc_file, wall_entities, user, mock_get_llm, mock_guardian, mock_git
    ):
        """FeasibilityChecker returning feasible=False raises ModificationError before LLM classification."""
        from writeback.services.feasibility_checker import FeasibilityResult

        with patch(
            "writeback.services.feasibility_checker.FeasibilityChecker.check",
            return_value=FeasibilityResult(
                feasible=False,
                reason="No target entities or property specified.",
            ),
        ):
            with patch("core.llm.resolve_model_name", return_value="llama3.1:8b"):
                svc = ModificationService(project, user=user)

                with pytest.raises(ModificationError, match="Request too vague"):
                    svc.propose(
                        "change everything",
                        user=user,
                        ifc_file=ifc_file,
                    )

    def test_propose_tier0_from_classifier_raises_modification_error(
        self, project, ifc_file, wall_entities, user, mock_get_llm, mock_guardian, mock_git
    ):
        """Classifier returning tier=0 raises ModificationError with the LLM's explanation."""
        tier0_intent = {
            "tier": 0,
            "explanation": "Cannot determine which entities or property to target.",
            "confidence": 0,
        }

        with patch(
            "writeback.services.intent_classifier.IntentClassifier.classify",
            return_value=tier0_intent,
        ):
            with patch("core.llm.resolve_model_name", return_value="llama3.1:8b"):
                svc = ModificationService(project, user=user)

                with pytest.raises(ModificationError, match="Ambiguous request"):
                    svc.propose(
                        "improve the model somehow",
                        user=user,
                        ifc_file=ifc_file,
                    )
