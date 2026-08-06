# writeback/tests/test_code_ack_gate.py
"""Tests for the code-review acknowledgement gate.

The gate follows the *code*, not the tier. A Tier 3 proposal built from
typed operations has a diff preview and nothing to read, so it approves
normally; anything that would execute generated Python must be
acknowledged first — whatever tier it claims to be.
"""

import pytest

from writeback.models import ModificationProposal
from writeback.tests.factories import ModificationProposalFactory

_CODE = "def modify_ifc(model):\n    return {'summary': 's', 'changes': []}"


def _run_code_journal() -> dict:
    """A journal whose only mutation is RUN_CODE (code lives in params)."""
    return {
        "schema_version": 1,
        "journal_id": "jrn_test",
        "ifc_file_id": "f",
        "source_tier": 3,
        "base_fingerprint": "",
        "captured_at": "2026-08-04T12:00:00+00:00",
        "mutations": [
            {
                "id": "mut_1",
                "op": "RUN_CODE",
                "global_id": "",
                "params": {"code": _CODE},
            }
        ],
    }


def _typed_ops_journal() -> dict:
    return {
        "schema_version": 1,
        "journal_id": "jrn_test",
        "ifc_file_id": "f",
        "source_tier": 3,
        "base_fingerprint": "",
        "captured_at": "2026-08-04T12:00:00+00:00",
        "mutations": [
            {
                "id": "mut_1",
                "op": "CREATE_ENTITY",
                "global_id": "",
                "entity_name": "Fire Zone A",
                "ifc_type": "IfcZone",
                "params": {},
            }
        ],
    }


@pytest.mark.django_db
class TestRequiresCodeAck:
    def test_typed_tier3_proposal_needs_no_ack(self, ifc_file, user):
        """The whole point of the pivot: typed RED ops are reviewable as a
        diff, so they no longer demand a code-review checkbox."""
        proposal = ModificationProposalFactory(
            ifc_file=ifc_file,
            created_by=user,
            tier=3,
            operation="OPS",
            changes=_typed_ops_journal(),
            intent_json={"tier": 3, "operation": "OPS", "ops": []},
        )

        assert proposal.requires_code_ack is False

    def test_proposal_with_code_in_intent_needs_ack(self, ifc_file, user):
        proposal = ModificationProposalFactory(
            ifc_file=ifc_file,
            created_by=user,
            tier=3,
            operation="CODE",
            intent_json={"tier": 3, "code": _CODE},
        )

        assert proposal.requires_code_ack is True

    def test_run_code_journal_without_intent_code_still_needs_ack(self, ifc_file, user):
        """THE airtightness case: the journal path moves code into the
        mutation params. Keying only on intent_json would silently disarm
        the gate for exactly the proposals that run code."""
        proposal = ModificationProposalFactory(
            ifc_file=ifc_file,
            created_by=user,
            tier=3,
            operation="CODE",
            changes=_run_code_journal(),
            intent_json={"tier": 3, "operation": "CODE"},  # no "code" key
        )

        assert "code" not in proposal.intent_json
        assert proposal.requires_code_ack is True

    def test_tier1_proposal_carrying_code_is_still_gated(self, ifc_file, user):
        """Gating on code rather than tier is strictly tighter — a
        mis-tiered proposal that carries code does not slip through."""
        proposal = ModificationProposalFactory(
            ifc_file=ifc_file,
            created_by=user,
            tier=1,
            operation="SET_PROPERTY",
            intent_json={"tier": 1, "code": _CODE},
        )

        assert proposal.requires_code_ack is True

    def test_ordinary_tier1_proposal_needs_no_ack(self, ifc_file, user):
        proposal = ModificationProposalFactory(
            ifc_file=ifc_file,
            created_by=user,
            tier=1,
            operation="SET_PROPERTY",
            intent_json={"tier": 1, "operation": "SET_PROPERTY"},
        )

        assert proposal.requires_code_ack is False

    def test_malformed_changes_does_not_crash(self, ifc_file, user):
        """`changes` is a JSONField and older rows hold a plain plan dict."""
        proposal = ModificationProposalFactory(
            ifc_file=ifc_file,
            created_by=user,
            tier=2,
            operation="PLAN",
            changes={"tier": 2, "plan": [{"step": 1}]},
            intent_json={"tier": 2},
        )

        assert proposal.requires_code_ack is False


@pytest.mark.django_db
class TestApproveGate:
    """The server-side check in _handle_approve is the source of truth."""

    def _setup(self, client, project, user):
        from chat.models import ChatSession

        client.force_login(user)
        return ChatSession.objects.create(
            project=project, user=user, mode=ChatSession.Mode.MODIFY, title="T"
        )

    def _approve(self, client, project, session, proposal):
        from django.urls import reverse

        url = reverse(
            "writeback:modify_session", kwargs={"pk": project.pk, "session_id": session.pk}
        )
        return client.post(url, {"action": "approve", "proposal_id": str(proposal.id)})

    def test_typed_tier3_approves_without_acknowledgement(
        self, client, project, ifc_file, user, monkeypatch
    ):
        from unittest.mock import MagicMock

        from writeback.services.modification_service import ModificationService

        session = self._setup(client, project, user)
        proposal = ModificationProposalFactory(
            ifc_file=ifc_file,
            created_by=user,
            tier=3,
            operation="OPS",
            status=ModificationProposal.Status.PENDING,
            changes=_typed_ops_journal(),
            intent_json={"tier": 3, "operation": "OPS"},
        )
        # The write itself is covered elsewhere; here we only care that the
        # request got past the ack gate.
        fake_commit = MagicMock(commit_hash="abc1234def")
        monkeypatch.setattr(ModificationService, "execute", lambda self, p: fake_commit)

        response = self._approve(client, project, session, proposal)

        assert response.status_code != 422, "Typed ops must not demand a code ack"

    def test_run_code_journal_is_refused_without_acknowledgement(
        self, client, project, ifc_file, user
    ):
        session = self._setup(client, project, user)
        proposal = ModificationProposalFactory(
            ifc_file=ifc_file,
            created_by=user,
            tier=3,
            operation="CODE",
            status=ModificationProposal.Status.PENDING,
            changes=_run_code_journal(),
            intent_json={"tier": 3, "operation": "CODE"},  # no "code" key
        )

        response = self._approve(client, project, session, proposal)

        assert response.status_code == 422
        assert response.json()["needs_review_ack"] is True
