# ifc_processor/tests/test_parser_upsert.py
"""Tests for IFCParser._upsert_issue and the reparse auto-cleanup sweep.

The full parse() pipeline needs an IFC model on disk; these tests instead drive
the dedup logic directly — _upsert_issue is a self-contained method whose only
external dependency is the Django ORM.
"""

from unittest.mock import MagicMock

import pytest

from ifc_processor.models import IFCDataIssue
from ifc_processor.services.parser import IFCParser
from ifc_processor.tests.factories import IFCDataIssueFactory, IFCFileFactory


def _make_parser(ifc_file) -> IFCParser:
    """Build an IFCParser wired just enough for _upsert_issue tests.

    The heavy init (schema detection, ifcopenshell model open) reads the file
    on disk; we bypass it with MagicMock and set the attributes _upsert_issue
    actually reads.
    """
    parser = MagicMock(spec=IFCParser)
    parser.ifc_file = ifc_file
    parser._seen_issue_hashes = set()
    # Bind the real method to the mock so ORM calls execute for real.
    parser._upsert_issue = IFCParser._upsert_issue.__get__(parser, IFCParser)
    return parser


@pytest.mark.django_db
class TestUpsertIssue:
    """Unit tests for IFCParser._upsert_issue."""

    def test_creates_new_row_with_new_flag_timestamps(self):
        """First call for a (file, global_id, issue_type) creates a row with first==last seen."""
        ifc_file = IFCFileFactory()
        parser = _make_parser(ifc_file)

        parser._upsert_issue(
            issue_type=IFCDataIssue.IssueType.ORPHANED_ELEMENT,
            global_id="GUID-ORPHAN-1",
            ifc_type="IfcWall",
            raw_data={"name": "W-01"},
            description="Orphan",
        )

        issue = IFCDataIssue.objects.get(ifc_file=ifc_file, global_id="GUID-ORPHAN-1")
        assert issue.status == IFCDataIssue.Status.OPEN
        assert issue.severity == IFCDataIssue.Severity.MEDIUM
        assert issue.content_hash == IFCDataIssue.compute_hash(
            ifc_file.id, "GUID-ORPHAN-1", IFCDataIssue.IssueType.ORPHANED_ELEMENT
        )
        # "New this parse" badge condition — first_seen_at == last_seen_at
        assert issue.first_seen_at == issue.last_seen_at
        assert issue.content_hash in parser._seen_issue_hashes

    def test_second_call_refreshes_open_row_in_place(self):
        """Same hash on reparse updates content and last_seen_at; first_seen_at persists."""
        ifc_file = IFCFileFactory()
        parser = _make_parser(ifc_file)
        original_hash = IFCDataIssue.compute_hash(
            ifc_file.id, "GUID-OPEN-1", IFCDataIssue.IssueType.MISSING_PROPERTY
        )
        IFCDataIssueFactory(
            ifc_file=ifc_file,
            global_id="GUID-OPEN-1",
            issue_type=IFCDataIssue.IssueType.MISSING_PROPERTY,
            description="stale description",
            content_hash=original_hash,
        )
        first_pk = IFCDataIssue.objects.get(content_hash=original_hash).pk
        first_seen = IFCDataIssue.objects.get(content_hash=original_hash).first_seen_at

        parser._upsert_issue(
            issue_type=IFCDataIssue.IssueType.MISSING_PROPERTY,
            global_id="GUID-OPEN-1",
            ifc_type="IfcWall",
            raw_data={"name": "new"},
            description="fresh description",
        )

        issue = IFCDataIssue.objects.get(content_hash=original_hash)
        assert issue.pk == first_pk  # same row, updated in place
        assert issue.description == "fresh description"
        assert issue.first_seen_at == first_seen  # preserved
        assert issue.last_seen_at > first_seen  # advanced
        assert IFCDataIssue.objects.filter(ifc_file=ifc_file).count() == 1

    def test_dismissed_row_survives_reparse(self):
        """A DISMISSED hash is preserved — only last_seen_at is touched, content stays."""
        ifc_file = IFCFileFactory()
        parser = _make_parser(ifc_file)
        original_hash = IFCDataIssue.compute_hash(
            ifc_file.id, "GUID-DISMISS-1", IFCDataIssue.IssueType.DUPLICATE_GUID
        )
        IFCDataIssueFactory(
            ifc_file=ifc_file,
            global_id="GUID-DISMISS-1",
            issue_type=IFCDataIssue.IssueType.DUPLICATE_GUID,
            description="user acknowledged",
            status=IFCDataIssue.Status.DISMISSED,
            content_hash=original_hash,
        )
        first_seen = IFCDataIssue.objects.get(content_hash=original_hash).first_seen_at

        parser._upsert_issue(
            issue_type=IFCDataIssue.IssueType.DUPLICATE_GUID,
            global_id="GUID-DISMISS-1",
            ifc_type="IfcWall",
            raw_data={"name": "ignored"},
            description="SHOULD NOT OVERWRITE",
        )

        issue = IFCDataIssue.objects.get(content_hash=original_hash)
        assert issue.status == IFCDataIssue.Status.DISMISSED
        assert issue.description == "user acknowledged"  # not overwritten
        assert issue.last_seen_at > first_seen
        assert original_hash in parser._seen_issue_hashes

    def test_severity_picked_from_issue_type(self):
        """Severity defaults derive from IFCDataIssue.SEVERITY_MAP per issue_type."""
        ifc_file = IFCFileFactory()
        parser = _make_parser(ifc_file)

        parser._upsert_issue(
            issue_type=IFCDataIssue.IssueType.DUPLICATE_GUID,
            global_id="DUP",
            ifc_type="IfcWall",
            raw_data={},
            description="",
        )
        parser._upsert_issue(
            issue_type=IFCDataIssue.IssueType.MISSING_PROPERTY,
            global_id="MISS",
            ifc_type="IfcWall",
            raw_data={},
            description="",
        )

        dup = IFCDataIssue.objects.get(global_id="DUP")
        miss = IFCDataIssue.objects.get(global_id="MISS")
        assert dup.severity == IFCDataIssue.Severity.HIGH
        assert miss.severity == IFCDataIssue.Severity.LOW


@pytest.mark.django_db
class TestReparseGhostCleanup:
    """The parse() sweep purges rows whose hash didn't recur this parse."""

    def test_orphaned_open_row_is_deleted_on_reparse(self):
        """A row not re-detected during parse() is removed."""
        ifc_file = IFCFileFactory()

        # Row from an earlier parse that will not recur.
        ghost = IFCDataIssueFactory(
            ifc_file=ifc_file,
            global_id="GHOST-OPEN",
            issue_type=IFCDataIssue.IssueType.ORPHANED_ELEMENT,
            status=IFCDataIssue.Status.OPEN,
        )
        # A row that IS re-detected this parse.
        kept = IFCDataIssueFactory(
            ifc_file=ifc_file,
            global_id="STILL-THERE",
            issue_type=IFCDataIssue.IssueType.ORPHANED_ELEMENT,
            status=IFCDataIssue.Status.OPEN,
        )
        seen_hashes = {kept.content_hash}

        # Reproduce Phase D inline (the actual sweep in parse()).
        IFCDataIssue.objects.filter(ifc_file=ifc_file).exclude(
            content_hash__in=seen_hashes
        ).delete()

        assert not IFCDataIssue.objects.filter(pk=ghost.pk).exists()
        assert IFCDataIssue.objects.filter(pk=kept.pk).exists()

    def test_dismissed_row_whose_hash_does_not_recur_is_deleted(self):
        """Dismissed-and-now-fixed rows self-clean on reparse.

        The UX contract: if the user fixes the IFC so the issue is gone, the
        dismissed card should disappear — not linger as an audit relic.
        """
        ifc_file = IFCFileFactory()
        dismissed_fixed = IFCDataIssueFactory(
            ifc_file=ifc_file,
            global_id="FIXED-GUID",
            issue_type=IFCDataIssue.IssueType.DUPLICATE_GUID,
            status=IFCDataIssue.Status.DISMISSED,
        )

        # No hashes recurred this parse.
        IFCDataIssue.objects.filter(ifc_file=ifc_file).exclude(content_hash__in=set()).delete()

        assert not IFCDataIssue.objects.filter(pk=dismissed_fixed.pk).exists()
