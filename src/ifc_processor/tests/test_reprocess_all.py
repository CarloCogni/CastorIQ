# ifc_processor/tests/test_reprocess_all.py
"""Tests for the reprocess_all fleet command (pipeline + embeddings mocked)."""

from __future__ import annotations

from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from ifc_processor.tests.factories import IFCFileFactory

pytestmark = pytest.mark.django_db

_PROCESSOR = "ifc_processor.services.processor.IFCProcessingService"
_EMBEDDER = "embeddings.services.embedding_service.EmbeddingService"


def _run(*args) -> str:
    out = StringIO()
    call_command("reprocess_all", *args, stdout=out)
    return out.getvalue()


@pytest.fixture
def embedding_up():
    """Embedding connectivity check succeeds."""
    with patch(_EMBEDDER) as mock_cls:
        mock_cls.return_value.embed_query.return_value = [0.1] * 1024
        yield mock_cls


def test_processes_only_completed_files(embedding_up):
    """Pending/failed files are outside the fleet run."""
    completed = IFCFileFactory(status="completed")
    IFCFileFactory(status="pending")
    IFCFileFactory(status="failed")

    with patch(_PROCESSOR) as mock_proc:
        mock_proc.return_value.run_pipeline.return_value = True
        output = _run()

    assert mock_proc.call_count == 1
    assert mock_proc.call_args[0][0].pk == completed.pk
    assert "1 reprocessed, 0 skipped, 0 failed" in output


def test_continues_past_a_failing_file(embedding_up):
    """One bad file must not abort the fleet; the run exits non-zero at the end."""
    IFCFileFactory(status="completed")
    IFCFileFactory(status="completed")

    with patch(_PROCESSOR) as mock_proc:
        mock_proc.return_value.run_pipeline.side_effect = [False, True]
        with pytest.raises(CommandError, match="1 file"):
            _run()

    assert mock_proc.call_count == 2


def test_project_filter_scopes_the_run(embedding_up):
    """--project limits the fleet to that project's files."""
    target = IFCFileFactory(status="completed")
    IFCFileFactory(status="completed")  # other project

    with patch(_PROCESSOR) as mock_proc:
        mock_proc.return_value.run_pipeline.return_value = True
        _run("--project", str(target.project.id))

    assert mock_proc.call_count == 1


def test_missing_media_is_skipped_not_failed(embedding_up):
    """A row whose file is gone from storage warns and skips, exit stays zero."""
    ifc_file = IFCFileFactory(status="completed")
    ifc_file.file.storage.delete(ifc_file.file.name)

    with patch(_PROCESSOR) as mock_proc:
        output = _run()

    assert mock_proc.call_count == 0
    assert "SKIP" in output
    assert "0 reprocessed, 1 skipped, 0 failed" in output


def test_dry_run_lists_without_processing(embedding_up):
    """--dry-run enumerates the fleet and touches nothing."""
    IFCFileFactory(status="completed")

    with patch(_PROCESSOR) as mock_proc:
        output = _run("--dry-run")

    assert mock_proc.call_count == 0
    assert "would reprocess" in output


def test_dead_embedding_service_fails_fast():
    """Ollama down → one clear error before any file is touched."""
    IFCFileFactory(status="completed")

    with patch(_EMBEDDER) as mock_embed:
        mock_embed.return_value.embed_query.return_value = None
        with patch(_PROCESSOR) as mock_proc:
            with pytest.raises(CommandError, match="[Ee]mbedding"):
                _run()

    assert mock_proc.call_count == 0
