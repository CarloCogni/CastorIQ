# core/tests/test_run_sample_pipeline_command.py
"""Tests for the `run_sample_pipeline` management command.

The command is the deferred (off-request) half of sample-project provisioning:
it (re)processes PENDING/FAILED IFC and Document rows in a user's Sample Project.
Both pipeline boundaries (IFC parse + embeddings, PDF parse + embeddings) are
mocked — they must never run for real in a unit test.
"""

from unittest.mock import patch

import pytest
from django.core.management import CommandError, call_command

from core.management.commands.provision_sample_project import SAMPLE_PROJECT_NAME
from documents.tests.factories import DocumentFactory
from environments.tests.factories import ProjectFactory, UserFactory
from ifc_processor.tests.factories import IFCFileFactory

PIPELINE = "ifc_processor.services.processor.IFCProcessingService.run_pipeline"
DOC_PROCESS = "documents.services.document_processor.DocumentProcessor.process"


@pytest.mark.django_db
def test_run_sample_pipeline_processes_pending_ifc_and_document():
    """PENDING IFC and Document rows in the Sample Project are both processed."""
    # Arrange
    user = UserFactory()
    project = ProjectFactory(owner=user, name=SAMPLE_PROJECT_NAME)
    IFCFileFactory(project=project, status="pending")
    DocumentFactory(project=project, status="pending")

    # Act
    with (
        patch(PIPELINE, return_value=True) as mock_ifc,
        patch(DOC_PROCESS, return_value=True) as mock_doc,
    ):
        call_command("run_sample_pipeline", str(user.pk))

    # Assert
    assert mock_ifc.call_count == 1
    assert mock_doc.call_count == 1


@pytest.mark.django_db
def test_run_sample_pipeline_reprocesses_failed_rows():
    """FAILED rows are retried so a crashed background thread is recoverable."""
    # Arrange
    user = UserFactory()
    project = ProjectFactory(owner=user, name=SAMPLE_PROJECT_NAME)
    IFCFileFactory(project=project, status="failed")

    # Act
    with patch(PIPELINE, return_value=True) as mock_ifc, patch(DOC_PROCESS):
        call_command("run_sample_pipeline", str(user.pk))

    # Assert
    assert mock_ifc.call_count == 1


@pytest.mark.django_db
def test_run_sample_pipeline_skips_completed_rows():
    """COMPLETED rows are left untouched — the command is idempotent."""
    # Arrange
    user = UserFactory()
    project = ProjectFactory(owner=user, name=SAMPLE_PROJECT_NAME)
    IFCFileFactory(project=project, status="completed")
    DocumentFactory(project=project, status="completed")

    # Act
    with patch(PIPELINE) as mock_ifc, patch(DOC_PROCESS) as mock_doc:
        call_command("run_sample_pipeline", str(user.pk))

    # Assert
    assert mock_ifc.call_count == 0
    assert mock_doc.call_count == 0


@pytest.mark.django_db
def test_run_sample_pipeline_one_file_failure_does_not_abort_the_rest():
    """An IFC pipeline exception is swallowed; remaining files still process."""
    # Arrange
    user = UserFactory()
    project = ProjectFactory(owner=user, name=SAMPLE_PROJECT_NAME)
    IFCFileFactory(project=project, status="pending")
    DocumentFactory(project=project, status="pending")

    # Act
    with (
        patch(PIPELINE, side_effect=RuntimeError("parse blew up")),
        patch(DOC_PROCESS, return_value=True) as mock_doc,
    ):
        call_command("run_sample_pipeline", str(user.pk))

    # Assert — the document still got processed despite the IFC failure
    assert mock_doc.call_count == 1


@pytest.mark.django_db
def test_run_sample_pipeline_no_sample_project_raises_command_error():
    """A user without a Sample Project is an operator error, surfaced loudly."""
    # Arrange
    user = UserFactory()

    # Act / Assert
    with pytest.raises(CommandError, match="no 'Sample Project'"):
        call_command("run_sample_pipeline", str(user.pk))


@pytest.mark.django_db
def test_run_sample_pipeline_unknown_user_raises_command_error():
    """An unknown user id is surfaced as a CommandError, not a 500."""
    with pytest.raises(CommandError, match="not found"):
        call_command("run_sample_pipeline", "nonexistent-user")
