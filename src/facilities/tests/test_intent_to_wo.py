# facilities/tests/test_intent_to_wo.py
"""Tests for the Intent-to-WO pipeline (M3.E).

LLM calls are mocked at the service level: ``_call_llm_classifier`` and
``_call_llm_planner`` are patched per-test to canned responses. The view
layer is exercised end-to-end (POST → propose → review modal → confirm),
including the bypass-guard test that confirm() walks through every WO's
transition chain rather than bulk-writing status.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.urls import reverse

from environments.tests.factories import (
    ProjectFactory,
    ProjectMembershipFactory,
    ProjectRoleFactory,
    UserFactory,
)
from facilities.models import (
    FMIntentProposal,
    WorkOrder,
    WorkOrderStatus,
)
from facilities.services.fm_intent_service import (
    FMIntentService,
    FMIntentValidationError,
    _Classification,
)
from facilities.tests.factories import FacilityAssetFactory

# ── Fixtures ──────────────────────────────────────────────────────────────


def _setup(role: str = "facilitiesmanager"):
    user = UserFactory()
    project = ProjectFactory()
    ProjectMembershipFactory(user=user, project=project, permission="editor")
    if role:
        ProjectRoleFactory(user=user, project=project, role=role)
    return user, project


def _three_ahus(project):
    """Three FacilityAsset rows in one project, named like rooftop AHUs."""
    assets = []
    for i in range(1, 4):
        a = FacilityAssetFactory(project=project, asset_tag=f"AHU-{i:02d}")
        assets.append(a)
    return assets


def _planner_response_for(assets, *, vendor: str = "ACME HVAC") -> dict:
    """Build a canned planner JSON for an 8-AHU-style scenario.

    The sample only includes as many WOs as the assets list — keeps tests
    deterministic without needing 8 fixture rows.
    """
    return {
        "explanation": f"Filter replacement for {len(assets)} AHUs.",
        "confidence": 87,
        "work_orders": [
            {
                "title": f"Filter replacement — {a.asset_tag}",
                "description": "Annual filter swap.",
                "affected_asset_id": str(a.pk),
                "category": "preventive",
                "priority": 2,
                "scheduled_start": "2026-04-28T08:00:00+00:00",
                "due_at": "2026-04-28T17:00:00+00:00",
                "assignee_vendor": vendor,
            }
            for a in assets
        ],
    }


# ── Service: propose() ────────────────────────────────────────────────────


@pytest.mark.django_db
class TestProposeService:
    def test_blank_message_raises(self):
        user, project = _setup()
        with pytest.raises(FMIntentValidationError):
            FMIntentService(project, user).propose("   ")

    def test_classifier_none_persists_rejected(self):
        user, project = _setup()
        with patch.object(FMIntentService, "_call_llm_classifier") as classifier:
            classifier.return_value = _Classification(kind="none", reason="It's a question")
            proposal = FMIntentService(project, user).propose("what's for lunch")
        assert proposal.status == FMIntentProposal.Status.REJECTED
        assert proposal.work_order_drafts == []

    def test_classifier_failure_persists_failed(self):
        user, project = _setup()
        with patch.object(
            FMIntentService, "_call_llm_classifier", side_effect=ValueError("bad json")
        ):
            proposal = FMIntentService(project, user).propose("schedule something")
        assert proposal.status == FMIntentProposal.Status.FAILED
        assert "Classifier failed" in proposal.error

    def test_planner_failure_persists_failed(self):
        user, project = _setup()
        with patch.object(
            FMIntentService,
            "_call_llm_classifier",
            return_value=_Classification(kind="batch", reason="Multi-WO"),
        ):
            with patch.object(
                FMIntentService, "_call_llm_planner", side_effect=ValueError("bad json")
            ):
                proposal = FMIntentService(project, user).propose("do all the things")
        assert proposal.status == FMIntentProposal.Status.FAILED
        assert "Planner failed" in proposal.error

    def test_happy_path_persists_pending(self):
        user, project = _setup()
        ahus = _three_ahus(project)
        with patch.object(
            FMIntentService,
            "_call_llm_classifier",
            return_value=_Classification(kind="batch", reason="Three AHUs"),
        ):
            with patch.object(
                FMIntentService,
                "_call_llm_planner",
                return_value=_planner_response_for(ahus),
            ):
                proposal = FMIntentService(project, user).propose(
                    "schedule filter replacement on all rooftop AHUs"
                )
        assert proposal.status == FMIntentProposal.Status.PENDING
        assert len(proposal.work_order_drafts) == 3
        assert proposal.confidence == 87


# ── Service: confirm() — bypass guard ────────────────────────────────────


@pytest.mark.django_db
class TestConfirmService:
    def test_confirm_creates_wo_and_walks_transition_chain(self):
        """confirm() must NOT bulk-write status — every WO needs a full status-event chain."""
        user, project = _setup()
        ahus = _three_ahus(project)
        with patch.object(
            FMIntentService,
            "_call_llm_classifier",
            return_value=_Classification(kind="batch", reason="x"),
        ):
            with patch.object(
                FMIntentService,
                "_call_llm_planner",
                return_value=_planner_response_for(ahus),
            ):
                svc = FMIntentService(project, user)
                proposal = svc.propose("schedule the filters")

        wos = svc.confirm(proposal)
        assert len(wos) == 3

        for wo in wos:
            wo.refresh_from_db()
            # All four transitions must have run: DRAFT → SUBMITTED → TRIAGED → ASSIGNED → SCHEDULED
            assert wo.status == WorkOrderStatus.SCHEDULED
            # 1 create event + 4 transition events = 5
            assert wo.status_events.count() == 5
            assert wo.source_intent_batch_id == proposal.pk

        # Audit row updated.
        proposal.refresh_from_db()
        assert proposal.status == FMIntentProposal.Status.APPLIED
        assert sorted(proposal.created_wo_ids) == sorted([str(w.pk) for w in wos])

    def test_confirm_drops_unchecked_rows(self):
        user, project = _setup()
        ahus = _three_ahus(project)
        with patch.object(
            FMIntentService,
            "_call_llm_classifier",
            return_value=_Classification(kind="batch", reason="x"),
        ):
            with patch.object(
                FMIntentService,
                "_call_llm_planner",
                return_value=_planner_response_for(ahus),
            ):
                svc = FMIntentService(project, user)
                proposal = svc.propose("schedule the filters")

        # Drop the second draft.
        edits = [
            proposal.work_order_drafts[0],
            proposal.work_order_drafts[2],
        ]
        wos = svc.confirm(proposal, edits=edits)
        assert len(wos) == 2

    def test_confirm_partial_chain_when_no_vendor(self):
        """When the draft has no vendor, the chain stops at TRIAGED (no assignee)."""
        user, project = _setup()
        ahus = _three_ahus(project)[:1]
        plan = _planner_response_for(ahus, vendor="")  # blank vendor
        plan["work_orders"][0]["scheduled_start"] = None
        with patch.object(
            FMIntentService,
            "_call_llm_classifier",
            return_value=_Classification(kind="batch", reason="x"),
        ):
            with patch.object(FMIntentService, "_call_llm_planner", return_value=plan):
                svc = FMIntentService(project, user)
                proposal = svc.propose("inspect AHU-01")

        wos = svc.confirm(proposal)
        assert len(wos) == 1
        wos[0].refresh_from_db()
        # DRAFT → SUBMITTED → TRIAGED ; no assignee, no schedule
        assert wos[0].status == WorkOrderStatus.TRIAGED

    def test_confirm_rejects_non_pending(self):
        user, project = _setup()
        proposal = FMIntentProposal.objects.create(
            project=project,
            user=user,
            user_message="x",
            status=FMIntentProposal.Status.APPLIED,
        )
        with pytest.raises(FMIntentValidationError):
            FMIntentService(project, user).confirm(proposal)

    def test_reject_marks_rejected(self):
        user, project = _setup()
        proposal = FMIntentProposal.objects.create(
            project=project,
            user=user,
            user_message="x",
            status=FMIntentProposal.Status.PENDING,
        )
        FMIntentService(project, user).reject(proposal, note="not now")
        proposal.refresh_from_db()
        assert proposal.status == FMIntentProposal.Status.REJECTED


# ── Asset slice / vendor list builders ────────────────────────────────────


@pytest.mark.django_db
class TestPromptContext:
    def test_asset_slice_keyword_pre_filter(self):
        """Assets that match the user message keywords appear at the top of the slice."""
        user, project = _setup()
        FacilityAssetFactory(project=project, asset_tag="AHU-01")
        FacilityAssetFactory(project=project, asset_tag="PUMP-04")
        slice_text = FMIntentService(project, user)._build_asset_slice("inspect rooftop AHU")
        # AHU should appear before PUMP because "ahu" matched.
        assert slice_text.index("AHU-01") < slice_text.index("PUMP-04")

    def test_asset_slice_empty_project(self):
        user, project = _setup()
        text = FMIntentService(project, user)._build_asset_slice("anything")
        assert "no assets" in text.lower()

    def test_vendor_list_distinct(self):
        from facilities.tests.factories import WorkOrderFactory

        user, project = _setup()
        WorkOrderFactory(project=project, assignee_vendor="ACME HVAC")
        WorkOrderFactory(project=project, assignee_vendor="ACME HVAC")
        WorkOrderFactory(project=project, assignee_vendor="Beta Co")
        listing = FMIntentService(project, user)._build_vendor_list()
        assert "ACME HVAC" in listing
        assert "Beta Co" in listing


# ── Views (HTTP-level integration) ────────────────────────────────────────


@pytest.mark.django_db
class TestIntentViews:
    def _login(self, client):
        user = UserFactory()
        project = ProjectFactory()
        ProjectMembershipFactory(user=user, project=project, permission="editor")
        ProjectRoleFactory(user=user, project=project, role="facilitiesmanager")
        client.force_login(user)
        return user, project

    def test_drawer_get(self, client):
        user, project = self._login(client)
        response = client.get(reverse("facilities:work_intent", args=[project.pk]))
        assert response.status_code == 200
        assert b"AI batch" in response.content

    def test_post_returns_review_modal(self, client):
        user, project = self._login(client)
        ahus = _three_ahus(project)
        with patch.object(
            FMIntentService,
            "_call_llm_classifier",
            return_value=_Classification(kind="batch", reason="x"),
        ):
            with patch.object(
                FMIntentService,
                "_call_llm_planner",
                return_value=_planner_response_for(ahus),
            ):
                response = client.post(
                    reverse("facilities:work_intent", args=[project.pk]),
                    data={"user_message": "schedule filter replacement on all rooftop AHUs"},
                )
        assert response.status_code == 200
        assert b"workIntentReviewModal" in response.content
        # All three drafts shown
        for ahu in ahus:
            assert ahu.asset_tag.encode() in response.content

    def test_confirm_creates_wos_and_redirects(self, client):
        user, project = self._login(client)
        ahus = _three_ahus(project)

        # Persist a proposal directly so the confirm POST has something to consume.
        proposal = FMIntentProposal.objects.create(
            project=project,
            user=user,
            user_message="schedule the filters",
            plan_json=_planner_response_for(ahus),
            confidence=85,
            status=FMIntentProposal.Status.PENDING,
        )

        post_data = {}
        for idx in range(len(ahus)):
            post_data[f"keep-{idx}"] = "on"

        response = client.post(
            reverse("facilities:work_intent_confirm", args=[project.pk, proposal.pk]),
            data=post_data,
        )
        assert response.status_code == 302  # redirect to kanban
        proposal.refresh_from_db()
        assert proposal.status == FMIntentProposal.Status.APPLIED
        assert WorkOrder.objects.filter(source_intent_batch_id=proposal.pk).count() == 3
