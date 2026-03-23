# ifc_processor/tests/test_parse_ifc_command.py
"""Tests for the parse_ifc management command — IFCParser always mocked."""

from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from ifc_processor.tests.factories import IFCFileFactory

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_parser():
    """Patches IFCParser so no real IFC file is read."""
    with patch("ifc_processor.management.commands.parse_ifc.IFCParser") as mock_cls:
        instance = mock_cls.return_value
        instance.parse.return_value = True
        instance.entities_created = 5
        yield instance


# ── Tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestParseIFCCommand:
    """Tests for the parse_ifc management command."""

    def test_parse_specific_file_by_id(self, mock_parser):
        """Providing an IFC file UUID parses that specific file."""
        ifc_file = IFCFileFactory(status="pending")

        out = StringIO()
        call_command("parse_ifc", str(ifc_file.id), stdout=out)

        mock_parser.parse.assert_called_once()

    def test_parse_all_pending_processes_each_file(self, mock_parser):
        """--all-pending flag processes all IFCFile objects with status=pending."""
        IFCFileFactory(status="pending")
        IFCFileFactory(status="pending")
        IFCFileFactory(status="completed")  # Should be skipped

        out = StringIO()
        call_command("parse_ifc", all_pending=True, stdout=out)

        # parse() should be called exactly twice (2 pending files)
        assert mock_parser.parse.call_count == 2

    def test_parse_nonexistent_id_raises_command_error(self):
        """Providing a UUID that doesn't match any IFCFile raises CommandError."""
        import uuid

        fake_id = str(uuid.uuid4())

        with pytest.raises(CommandError, match="not found"):
            call_command("parse_ifc", fake_id)

    def test_parse_completed_file_skips_without_reprocess(self, mock_parser):
        """Completed files are skipped unless --reprocess is given."""
        ifc_file = IFCFileFactory(status="completed")

        out = StringIO()
        call_command("parse_ifc", str(ifc_file.id), stdout=out)

        # parse() should NOT be called for already-completed files
        mock_parser.parse.assert_not_called()
        assert "already processed" in out.getvalue().lower()

    def test_reprocess_flag_parses_completed_file(self, mock_parser):
        """--reprocess forces re-parsing even for completed files."""
        ifc_file = IFCFileFactory(status="completed")

        out = StringIO()
        call_command("parse_ifc", str(ifc_file.id), reprocess=True, stdout=out)

        mock_parser.parse.assert_called_once()

    def test_no_arguments_outputs_warning(self, mock_parser):
        """Invoking command with no arguments outputs a warning (no crash)."""
        out = StringIO()
        call_command("parse_ifc", stdout=out)

        mock_parser.parse.assert_not_called()
        assert "No IFC file specified" in out.getvalue()

    def test_parse_failure_outputs_error_message(self):
        """When IFCParser.parse() returns False, command outputs an error message."""
        ifc_file = IFCFileFactory(status="pending")

        with patch("ifc_processor.management.commands.parse_ifc.IFCParser") as mock_cls:
            instance = mock_cls.return_value
            instance.parse.return_value = False
            instance.entities_created = 0

            out = StringIO()
            call_command("parse_ifc", str(ifc_file.id), stdout=out)

        output = out.getvalue()
        # Command should mention failure
        assert "Failed" in output or "failed" in output

    def test_parse_all_pending_with_no_files_outputs_zero_count(self, mock_parser):
        """--all-pending with no pending files runs cleanly and reports 0 files."""
        out = StringIO()
        call_command("parse_ifc", all_pending=True, stdout=out)

        mock_parser.parse.assert_not_called()
        assert "0" in out.getvalue()
