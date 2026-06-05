# documents/tests/test_check_document_files.py
"""Tests for the `check_document_files` management command."""

from __future__ import annotations

from io import StringIO

import pytest
from django.core.files.storage import default_storage
from django.core.management import call_command

from documents.models import Document
from documents.tests.factories import DocumentFactory


@pytest.mark.django_db
def test_reports_no_orphans_when_files_exist():
    """All Document files present on disk → exit 0, success line, no orphan rows."""
    DocumentFactory()
    DocumentFactory()

    out = StringIO()
    call_command("check_document_files", stdout=out)

    output = out.getvalue()
    assert "No orphan documents found." in output


@pytest.mark.django_db
def test_reports_orphan_when_file_missing():
    """Document row present, file deleted from storage → reported and exit 1."""
    doc = DocumentFactory()
    # Sanity-check the file got persisted before we yank it.
    assert default_storage.exists(doc.file.name)
    default_storage.delete(doc.file.name)
    assert not default_storage.exists(doc.file.name)

    out = StringIO()
    with pytest.raises(SystemExit) as exc:
        call_command("check_document_files", stdout=out)
    assert exc.value.code == 1

    output = out.getvalue()
    assert str(doc.id) in output
    assert "Found 1 orphan document(s)." in output
    # Row must not have been deleted in the default report-only run.
    assert Document.objects.filter(id=doc.id).exists()


@pytest.mark.django_db
def test_project_filter_scopes_the_scan():
    """--project <uuid> restricts orphans to that project."""
    orphan_in_target = DocumentFactory()
    default_storage.delete(orphan_in_target.file.name)

    orphan_elsewhere = DocumentFactory()
    default_storage.delete(orphan_elsewhere.file.name)

    out = StringIO()
    with pytest.raises(SystemExit):
        call_command("check_document_files", project=str(orphan_in_target.project_id), stdout=out)

    output = out.getvalue()
    assert str(orphan_in_target.id) in output
    assert str(orphan_elsewhere.id) not in output


@pytest.mark.django_db
def test_delete_orphans_removes_the_rows():
    """--delete-orphans wipes the orphan rows after reporting."""
    doc = DocumentFactory()
    default_storage.delete(doc.file.name)

    out = StringIO()
    with pytest.raises(SystemExit):
        call_command("check_document_files", delete_orphans=True, stdout=out)

    assert not Document.objects.filter(id=doc.id).exists()
    assert "Deleted 1 orphan Document row(s)." in out.getvalue()
