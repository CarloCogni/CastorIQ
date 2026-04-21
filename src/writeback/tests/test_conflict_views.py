# writeback/tests/test_conflict_views.py
"""Integration tests for conflict CRUD views."""

import uuid

import pytest
from django.test import Client
from django.urls import reverse

from ifc_processor.models import IFCDataIssue
from writeback.models import Conflict
from writeback.tests.factories import ConflictFactory, ScanRunFactory
from writeback.views import ConflictsView

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def auth_client(user):
    """Django test client logged in as the project owner."""
    client = Client()
    client.force_login(user)
    return client


@pytest.fixture()
def other_user():
    """A second user who does NOT own the project."""
    from environments.tests.factories import UserFactory

    return UserFactory()


@pytest.fixture()
def other_client(other_user):
    """Test client logged in as a non-owner."""
    client = Client()
    client.force_login(other_user)
    return client


@pytest.fixture()
def conflict(project, wall_entities):
    """A single OPEN conflict linked to the first wall entity."""
    return ConflictFactory(
        project=project,
        ifc_entity=wall_entities[0],
        property_name="FireRating",
    )


@pytest.fixture()
def conflicts_batch(project, wall_entities):
    """Three OPEN conflicts for bulk operation tests."""
    return [
        ConflictFactory(
            project=project,
            ifc_entity=wall_entities[i],
            property_name="FireRating",
        )
        for i in range(3)
    ]


# ===================================================================
# DismissConflictView
# ===================================================================


class TestDismissConflictView:
    """POST /<pk>/conflicts/<conflict_id>/dismiss/"""

    def test_dismiss_sets_status_dismissed(self, auth_client, project, conflict):
        """Dismissing an OPEN conflict sets its status to DISMISSED."""
        url = reverse(
            "writeback:dismiss_conflict",
            kwargs={"pk": project.pk, "conflict_id": conflict.pk},
        )
        resp = auth_client.post(url)

        assert resp.status_code == 200
        assert resp.json()["status"] == "dismissed"
        conflict.refresh_from_db()
        assert conflict.status == Conflict.Status.DISMISSED

    def test_dismiss_nonexistent_conflict_returns_404(self, auth_client, project):
        """Dismissing a non-existent conflict returns 404."""
        url = reverse(
            "writeback:dismiss_conflict",
            kwargs={"pk": project.pk, "conflict_id": uuid.uuid4()},
        )
        assert auth_client.post(url).status_code == 404

    def test_dismiss_requires_auth(self, project, conflict):
        """Unauthenticated requests redirect to login."""
        url = reverse(
            "writeback:dismiss_conflict",
            kwargs={"pk": project.pk, "conflict_id": conflict.pk},
        )
        resp = Client().post(url)
        assert resp.status_code == 302

    def test_dismiss_denies_non_owner(self, other_client, project, conflict):
        """Non-owner gets 403."""
        url = reverse(
            "writeback:dismiss_conflict",
            kwargs={"pk": project.pk, "conflict_id": conflict.pk},
        )
        assert other_client.post(url).status_code == 403


# ===================================================================
# IgnoreConflictView
# ===================================================================


class TestRestoreConflictView:
    """POST /<pk>/conflicts/<conflict_id>/restore/"""

    def test_restore_sets_status_open(self, auth_client, project, wall_entities):
        """Restoring a DISMISSED conflict flips it back to OPEN and clears resolution metadata."""
        from django.utils import timezone

        from writeback.tests.factories import ConflictFactory

        c = ConflictFactory(
            project=project,
            ifc_entity=wall_entities[0],
            status=Conflict.Status.DISMISSED,
            resolved_at=timezone.now(),
            resolution_note="accidentally dismissed",
        )

        url = reverse(
            "writeback:restore_conflict",
            kwargs={"pk": project.pk, "conflict_id": c.pk},
        )
        resp = auth_client.post(url)

        assert resp.status_code == 200
        assert resp.json()["status"] == "open"
        c.refresh_from_db()
        assert c.status == Conflict.Status.OPEN
        assert c.resolved_at is None
        assert c.resolution_note == ""

    def test_restore_ignored_conflict(self, auth_client, project, wall_entities):
        """Ignored conflicts can also be restored."""
        from writeback.tests.factories import ConflictFactory

        c = ConflictFactory(
            project=project, ifc_entity=wall_entities[0], status=Conflict.Status.IGNORED
        )
        url = reverse(
            "writeback:restore_conflict",
            kwargs={"pk": project.pk, "conflict_id": c.pk},
        )
        resp = auth_client.post(url)

        assert resp.status_code == 200
        c.refresh_from_db()
        assert c.status == Conflict.Status.OPEN

    def test_restore_denies_non_owner(self, other_client, project, conflict):
        """Non-owner gets 403."""
        url = reverse(
            "writeback:restore_conflict",
            kwargs={"pk": project.pk, "conflict_id": conflict.pk},
        )
        assert other_client.post(url).status_code == 403

    def test_restore_missing_conflict_returns_404(self, auth_client, project):
        """Unknown conflict id returns 404."""
        url = reverse(
            "writeback:restore_conflict",
            kwargs={"pk": project.pk, "conflict_id": uuid.uuid4()},
        )
        assert auth_client.post(url).status_code == 404


class TestIgnoreConflictView:
    """POST /<pk>/conflicts/<conflict_id>/ignore/"""

    def test_ignore_sets_status_ignored(self, auth_client, project, conflict):
        """Ignoring an OPEN conflict sets its status to IGNORED."""
        url = reverse(
            "writeback:ignore_conflict",
            kwargs={"pk": project.pk, "conflict_id": conflict.pk},
        )
        resp = auth_client.post(url)

        assert resp.status_code == 200
        assert resp.json()["status"] == "ignored"
        conflict.refresh_from_db()
        assert conflict.status == Conflict.Status.IGNORED

    def test_ignore_denies_non_owner(self, other_client, project, conflict):
        """Non-owner gets 403."""
        url = reverse(
            "writeback:ignore_conflict",
            kwargs={"pk": project.pk, "conflict_id": conflict.pk},
        )
        assert other_client.post(url).status_code == 403


# ===================================================================
# BulkDismissView
# ===================================================================


class TestBulkDismissView:
    """POST /<pk>/conflicts/bulk-dismiss/"""

    def test_bulk_dismiss_specific_ids(self, auth_client, project, conflicts_batch):
        """Only the specified conflicts are dismissed."""
        target = conflicts_batch[:2]
        untouched = conflicts_batch[2]
        ids_csv = ",".join(str(c.pk) for c in target)

        url = reverse("writeback:bulk_dismiss", kwargs={"pk": project.pk})
        resp = auth_client.post(url, {"conflict_ids": ids_csv})

        assert resp.status_code == 200
        assert resp.json()["count"] == 2
        for c in target:
            c.refresh_from_db()
            assert c.status == Conflict.Status.DISMISSED
        untouched.refresh_from_db()
        assert untouched.status == Conflict.Status.OPEN

    def test_bulk_dismiss_all(self, auth_client, project, conflicts_batch):
        """Passing 'all' dismisses every OPEN conflict."""
        url = reverse("writeback:bulk_dismiss", kwargs={"pk": project.pk})
        resp = auth_client.post(url, {"conflict_ids": "all"})

        assert resp.json()["count"] == 3
        for c in conflicts_batch:
            c.refresh_from_db()
            assert c.status == Conflict.Status.DISMISSED

    def test_bulk_dismiss_skips_non_open(self, auth_client, project, conflicts_batch):
        """Already-IGNORED conflicts are not affected by bulk dismiss."""
        conflicts_batch[0].status = Conflict.Status.IGNORED
        conflicts_batch[0].save(update_fields=["status"])

        url = reverse("writeback:bulk_dismiss", kwargs={"pk": project.pk})
        resp = auth_client.post(url, {"conflict_ids": "all"})

        assert resp.json()["count"] == 2
        conflicts_batch[0].refresh_from_db()
        assert conflicts_batch[0].status == Conflict.Status.IGNORED


# ===================================================================
# BulkIgnoreView
# ===================================================================


class TestBulkIgnoreView:
    """POST /<pk>/conflicts/bulk-ignore/"""

    def test_bulk_ignore_specific_ids(self, auth_client, project, conflicts_batch):
        """Specified OPEN conflicts are set to IGNORED."""
        ids_csv = ",".join(str(c.pk) for c in conflicts_batch[:2])
        url = reverse("writeback:bulk_ignore_conflicts", kwargs={"pk": project.pk})
        resp = auth_client.post(url, {"conflict_ids": ids_csv})

        assert resp.status_code == 200
        assert resp.json()["count"] == 2
        for c in conflicts_batch[:2]:
            c.refresh_from_db()
            assert c.status == Conflict.Status.IGNORED

    def test_bulk_ignore_skips_non_open(self, auth_client, project, conflicts_batch):
        """Only OPEN conflicts are ignored; DISMISSED ones stay."""
        conflicts_batch[0].status = Conflict.Status.DISMISSED
        conflicts_batch[0].save(update_fields=["status"])

        ids_csv = ",".join(str(c.pk) for c in conflicts_batch)
        url = reverse("writeback:bulk_ignore_conflicts", kwargs={"pk": project.pk})
        resp = auth_client.post(url, {"conflict_ids": ids_csv})

        assert resp.json()["count"] == 2
        conflicts_batch[0].refresh_from_db()
        assert conflicts_batch[0].status == Conflict.Status.DISMISSED


# ===================================================================
# BulkResolveView
# ===================================================================


class TestBulkResolveView:
    """POST /<pk>/conflicts/bulk-resolve/"""

    def test_bulk_resolve_specific_ids(self, auth_client, user, project, conflicts_batch):
        """Resolved conflicts get resolved_by, resolved_at, and resolution_note."""
        target = conflicts_batch[:2]
        ids_csv = ",".join(str(c.pk) for c in target)
        url = reverse("writeback:bulk_resolve_conflicts", kwargs={"pk": project.pk})
        resp = auth_client.post(url, {"conflict_ids": ids_csv})

        assert resp.status_code == 200
        assert resp.json()["count"] == 2
        for c in target:
            c.refresh_from_db()
            assert c.status == Conflict.Status.RESOLVED
            assert c.resolved_by == user
            assert c.resolved_at is not None
            assert c.resolution_note == "Manually resolved"

    def test_bulk_resolve_all(self, auth_client, project, conflicts_batch):
        """Passing 'all' resolves every OPEN conflict."""
        url = reverse("writeback:bulk_resolve_conflicts", kwargs={"pk": project.pk})
        resp = auth_client.post(url, {"conflict_ids": "all"})

        assert resp.json()["count"] == 3

    def test_bulk_resolve_skips_non_open(self, auth_client, project, conflicts_batch):
        """DISMISSED conflict stays DISMISSED during bulk resolve."""
        conflicts_batch[0].status = Conflict.Status.DISMISSED
        conflicts_batch[0].save(update_fields=["status"])

        url = reverse("writeback:bulk_resolve_conflicts", kwargs={"pk": project.pk})
        resp = auth_client.post(url, {"conflict_ids": "all"})

        assert resp.json()["count"] == 2
        conflicts_batch[0].refresh_from_db()
        assert conflicts_batch[0].status == Conflict.Status.DISMISSED


# ===================================================================
# DeleteAllConflictsView
# ===================================================================


class TestDeleteAllConflictsView:
    """POST /<pk>/conflicts/delete-all/"""

    def test_delete_all_removes_all_statuses(self, auth_client, project, conflicts_batch):
        """Delete-all hard-deletes conflicts regardless of status."""
        conflicts_batch[0].status = Conflict.Status.RESOLVED
        conflicts_batch[0].save(update_fields=["status"])
        conflicts_batch[1].status = Conflict.Status.IGNORED
        conflicts_batch[1].save(update_fields=["status"])

        url = reverse("writeback:delete_all_conflicts", kwargs={"pk": project.pk})
        resp = auth_client.post(url)

        assert resp.status_code == 200
        assert resp.json()["count"] == 3
        assert project.conflicts.count() == 0

    def test_delete_all_returns_count(self, auth_client, project, conflict):
        """Response includes the number of deleted records."""
        url = reverse("writeback:delete_all_conflicts", kwargs={"pk": project.pk})
        resp = auth_client.post(url)

        assert resp.json() == {"status": "deleted", "count": 1}

    def test_delete_all_denies_non_owner(self, other_client, project, conflict):
        """Non-owner gets 403."""
        url = reverse("writeback:delete_all_conflicts", kwargs={"pk": project.pk})
        assert other_client.post(url).status_code == 403


# ===================================================================
# Auto-resolve on approval (views.py:315-326)
# ===================================================================


class TestAutoResolveOnApproval:
    """When a proposal with linked_conflict_ids is approved, those conflicts
    are auto-resolved with the commit hash in the resolution note."""

    def test_approve_resolves_linked_conflicts(
        self, auth_client, user, project, ifc_file, conflicts_batch, monkeypatch
    ):
        """Approving a proposal auto-resolves its linked OPEN conflicts."""
        from writeback.models import GitCommit, ModificationProposal

        proposal = ModificationProposal.objects.create(
            ifc_file=ifc_file,
            created_by=user,
            request_text="Fix fire rating",
            explanation="Correct FireRating values",
            changes=[],
            diff_preview="EI60 → EI120",
            status="pending",
            tier=1,
            operation="SET_PROPERTY",
            linked_conflict_ids=[str(c.pk) for c in conflicts_batch],
        )

        fake_commit = GitCommit.objects.create(
            ifc_file=ifc_file,
            commit_hash="abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
            message="Fix FireRating",
            author=user,
        )

        monkeypatch.setattr(
            "writeback.services.modification_service.ModificationService.execute",
            lambda self, proposal: fake_commit,
        )

        url = reverse("writeback:modify", kwargs={"pk": project.pk})
        resp = auth_client.post(url, {"action": "approve", "proposal_id": str(proposal.pk)})

        assert resp.status_code == 200
        for c in conflicts_batch:
            c.refresh_from_db()
            assert c.status == Conflict.Status.RESOLVED
            assert c.resolved_by == user
            assert "abcdef12" in c.resolution_note

    def test_approve_skips_non_open_linked_conflicts(
        self, auth_client, user, project, ifc_file, conflicts_batch, monkeypatch
    ):
        """Already-DISMISSED linked conflicts stay DISMISSED after approval."""
        from writeback.models import GitCommit, ModificationProposal

        conflicts_batch[0].status = Conflict.Status.DISMISSED
        conflicts_batch[0].save(update_fields=["status"])

        proposal = ModificationProposal.objects.create(
            ifc_file=ifc_file,
            created_by=user,
            request_text="Fix fire rating",
            explanation="Correct FireRating values",
            changes=[],
            diff_preview="EI60 → EI120",
            status="pending",
            tier=1,
            operation="SET_PROPERTY",
            linked_conflict_ids=[str(c.pk) for c in conflicts_batch],
        )

        fake_commit = GitCommit.objects.create(
            ifc_file=ifc_file,
            commit_hash="deadbeef1234567890abcdef1234567890abcdef1234567890abcdef12345678",
            message="Fix FireRating",
            author=user,
        )

        monkeypatch.setattr(
            "writeback.services.modification_service.ModificationService.execute",
            lambda self, proposal: fake_commit,
        )

        url = reverse("writeback:modify", kwargs={"pk": project.pk})
        resp = auth_client.post(url, {"action": "approve", "proposal_id": str(proposal.pk)})

        assert resp.status_code == 200
        conflicts_batch[0].refresh_from_db()
        assert conflicts_batch[0].status == Conflict.Status.DISMISSED
        for c in conflicts_batch[1:]:
            c.refresh_from_db()
            assert c.status == Conflict.Status.RESOLVED


# ===================================================================
# _build_fix_prompt (Fix in Modify flow)
# ===================================================================


class TestBuildFixPrompt:
    """ConflictsView._build_fix_prompt generates modify prompts from conflicts."""

    def test_fix_prompt_for_missing_value(self, wall_entities):
        """Missing IFC value generates an 'Add ...' prompt."""
        conflict = Conflict(
            ifc_value="missing",
            document_value="EI120",
            property_name="FireRating",
            description="FireRating not set",
            ifc_entity=wall_entities[0],
        )
        prompt = ConflictsView._build_fix_prompt(conflict, [wall_entities[0]])

        assert prompt.startswith("Add FireRating")
        assert "EI120" in prompt
        assert wall_entities[0].name in prompt

    def test_fix_prompt_for_value_mismatch(self, wall_entities):
        """Value mismatch generates a 'Set ... from ... to ...' prompt."""
        conflict = Conflict(
            ifc_value="EI60",
            document_value="EI120",
            property_name="FireRating",
            description="FireRating mismatch",
            ifc_entity=wall_entities[0],
        )
        prompt = ConflictsView._build_fix_prompt(conflict, [wall_entities[0]])

        assert prompt.startswith("Set FireRating")
        assert "EI60" in prompt
        assert "EI120" in prompt

    def test_fix_prompt_uses_suggested_fix(self):
        """When suggested_fix is present, it is returned verbatim."""
        conflict = Conflict(suggested_fix="Change FireRating to EI120 on all walls")
        prompt = ConflictsView._build_fix_prompt(conflict, [])

        assert prompt == "Change FireRating to EI120 on all walls"

    def test_fix_prompt_multiple_entities(self, wall_entities):
        """Multiple entities produce a list in the prompt."""
        conflict = Conflict(
            ifc_value="EI60",
            document_value="EI120",
            property_name="FireRating",
            description="Mismatch",
            ifc_entity=wall_entities[0],
        )
        prompt = ConflictsView._build_fix_prompt(conflict, wall_entities[:3])

        assert "elements:" in prompt
        for entity in wall_entities[:3]:
            assert entity.name in prompt


# ===================================================================
# ConflictsView GET (template context)
# ===================================================================


class TestConflictsViewGET:
    """GET /<pk>/conflicts/ renders grouped conflicts and data issues."""

    def test_conflicts_page_renders_grouped_by_status(self, auth_client, project, conflicts_batch):
        """Context includes grouped_by_status, status_counts, and flags."""
        url = reverse("writeback:conflicts", kwargs={"pk": project.pk})
        resp = auth_client.get(url)

        assert resp.status_code == 200
        ctx = resp.context
        assert "grouped_by_status" in ctx
        assert "status_counts" in ctx
        assert ctx["status_counts"]["open"] == 3
        assert ctx["open_conflicts"] is True
        assert ctx["has_any_conflicts"] is True

    def test_data_issues_rendered(self, auth_client, project, ifc_file):
        """OPEN IFCDataIssue records appear in the open_issues context list."""
        IFCDataIssue.objects.create(
            ifc_file=ifc_file,
            issue_type=IFCDataIssue.IssueType.DUPLICATE_GUID,
            global_id="GUID-DUP-001",
            ifc_type="IfcWall",
            raw_data={"Name": "Wall-Dup"},
            description="Duplicate GlobalID found",
            content_hash=IFCDataIssue.compute_hash(
                ifc_file.id, "GUID-DUP-001", IFCDataIssue.IssueType.DUPLICATE_GUID
            ),
        )

        url = reverse("writeback:conflicts", kwargs={"pk": project.pk})
        resp = auth_client.get(url)

        assert resp.status_code == 200
        assert len(resp.context["open_issues"]) == 1
        assert resp.context["data_issue_type_counts"]["duplicate_guid"] == 1
        assert resp.context["data_issue_type_counts"]["all"] == 1

    def test_last_scan_run_in_context(self, auth_client, project):
        """Completed ScanRun appears as last_scan_run."""
        scan = ScanRunFactory(project=project, status="completed")

        url = reverse("writeback:conflicts", kwargs={"pk": project.pk})
        resp = auth_client.get(url)

        assert resp.context["last_scan_run"] == scan

    def test_grouping_key_includes_ifc_type(self, auth_client, project, ifc_file):
        """Two conflicts with identical (title, ifc_value, document_value) but
        different ifc_type do NOT collapse into a single group — each IFC type
        gets its own card. Regression for the wall/beam confusion.
        """
        from ifc_processor.tests.factories import IFCEntityFactory

        wall = IFCEntityFactory(ifc_file=ifc_file, ifc_type="IfcWall", name="outer-wall")
        beam = IFCEntityFactory(ifc_file=ifc_file, ifc_type="IfcBeam", name="girder")

        common = {
            "project": project,
            "title": "Missing fire rating for external walls",
            "ifc_value": "absent (no property sets)",
            "document_value": "EI60",
            "severity": "critical",
        }
        ConflictFactory(ifc_entity=wall, **common)
        ConflictFactory(ifc_entity=beam, **common)

        url = reverse("writeback:conflicts", kwargs={"pk": project.pk})
        resp = auth_client.get(url)

        assert resp.status_code == 200
        open_groups = resp.context["grouped_by_status"]["open"]
        # One group per ifc_type — each should have count == 1
        assert len(open_groups) == 2
        representatives_by_type = {g["representative"].ifc_entity.ifc_type for g in open_groups}
        assert representatives_by_type == {"IfcWall", "IfcBeam"}


# ===================================================================
# Data Issue dismiss / restore / bulk-dismiss views
# ===================================================================


def _make_issue(
    ifc_file,
    *,
    issue_type=IFCDataIssue.IssueType.ORPHANED_ELEMENT,
    gid=None,
    status=IFCDataIssue.Status.OPEN,
):
    gid = gid or f"GID-{uuid.uuid4().hex[:8]}"
    return IFCDataIssue.objects.create(
        ifc_file=ifc_file,
        issue_type=issue_type,
        global_id=gid,
        ifc_type="IfcWall",
        raw_data={"name": gid},
        description="test",
        severity=IFCDataIssue.Severity.MEDIUM,
        status=status,
        content_hash=IFCDataIssue.compute_hash(ifc_file.id, gid, issue_type),
    )


class TestDismissDataIssueView:
    """POST /<pk>/data-issues/<issue_id>/dismiss/"""

    def test_dismiss_flips_status_and_returns_card(self, auth_client, project, ifc_file):
        """Dismiss flips status to DISMISSED and returns the card partial HTML."""
        issue = _make_issue(ifc_file)
        url = reverse(
            "writeback:dismiss_data_issue",
            kwargs={"pk": project.pk, "issue_id": issue.id},
        )

        resp = auth_client.post(url)

        assert resp.status_code == 200
        issue.refresh_from_db()
        assert issue.status == IFCDataIssue.Status.DISMISSED
        # Returned partial carries the new status in a data-attribute
        assert b'data-status="dismissed"' in resp.content

    def test_dismiss_requires_auth(self, project, ifc_file):
        """Anonymous users get redirected."""
        issue = _make_issue(ifc_file)
        url = reverse(
            "writeback:dismiss_data_issue",
            kwargs={"pk": project.pk, "issue_id": issue.id},
        )
        assert Client().post(url).status_code == 302

    def test_dismiss_denies_non_owner(self, other_client, project, ifc_file):
        """Non-members get 403."""
        issue = _make_issue(ifc_file)
        url = reverse(
            "writeback:dismiss_data_issue",
            kwargs={"pk": project.pk, "issue_id": issue.id},
        )
        assert other_client.post(url).status_code == 403


class TestRestoreDataIssueView:
    """POST /<pk>/data-issues/<issue_id>/restore/"""

    def test_restore_flips_status_back_to_open(self, auth_client, project, ifc_file):
        """Restore moves a DISMISSED issue back to OPEN."""
        issue = _make_issue(ifc_file, status=IFCDataIssue.Status.DISMISSED)
        url = reverse(
            "writeback:restore_data_issue",
            kwargs={"pk": project.pk, "issue_id": issue.id},
        )

        resp = auth_client.post(url)

        assert resp.status_code == 200
        issue.refresh_from_db()
        assert issue.status == IFCDataIssue.Status.OPEN
        assert b'data-status="open"' in resp.content


class TestBulkDismissDataIssuesView:
    """POST /<pk>/data-issues/bulk-dismiss/"""

    def test_bulk_dismiss_without_filter_dismisses_all_open(self, auth_client, project, ifc_file):
        """Omitting issue_type dismisses every OPEN issue for the project."""
        _make_issue(ifc_file, issue_type=IFCDataIssue.IssueType.ORPHANED_ELEMENT, gid="A")
        _make_issue(ifc_file, issue_type=IFCDataIssue.IssueType.MISSING_PROPERTY, gid="B")
        already_dismissed = _make_issue(
            ifc_file,
            issue_type=IFCDataIssue.IssueType.DUPLICATE_GUID,
            gid="C",
            status=IFCDataIssue.Status.DISMISSED,
        )

        url = reverse("writeback:bulk_dismiss_data_issues", kwargs={"pk": project.pk})
        resp = auth_client.post(url)

        assert resp.status_code == 200
        assert resp.json()["count"] == 2  # only the 2 OPEN rows flipped
        assert (
            IFCDataIssue.objects.filter(ifc_file=ifc_file, status=IFCDataIssue.Status.OPEN).count()
            == 0
        )
        # Pre-dismissed row is untouched
        already_dismissed.refresh_from_db()
        assert already_dismissed.status == IFCDataIssue.Status.DISMISSED

    def test_bulk_dismiss_respects_issue_type_filter(self, auth_client, project, ifc_file):
        """Passing issue_type scopes the bulk action to matching rows only."""
        orphan = _make_issue(ifc_file, issue_type=IFCDataIssue.IssueType.ORPHANED_ELEMENT, gid="A")
        missing = _make_issue(ifc_file, issue_type=IFCDataIssue.IssueType.MISSING_PROPERTY, gid="B")

        url = reverse("writeback:bulk_dismiss_data_issues", kwargs={"pk": project.pk})
        resp = auth_client.post(url, {"issue_type": "orphaned_element"})

        assert resp.status_code == 200
        assert resp.json()["count"] == 1
        orphan.refresh_from_db()
        missing.refresh_from_db()
        assert orphan.status == IFCDataIssue.Status.DISMISSED
        assert missing.status == IFCDataIssue.Status.OPEN

    def test_bulk_dismiss_denies_non_owner(self, other_client, project, ifc_file):
        """Non-member bulk dismiss returns 403 and does not mutate rows."""
        issue = _make_issue(ifc_file)
        url = reverse("writeback:bulk_dismiss_data_issues", kwargs={"pk": project.pk})

        assert other_client.post(url).status_code == 403
        issue.refresh_from_db()
        assert issue.status == IFCDataIssue.Status.OPEN
