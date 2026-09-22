# writeback/tests/test_sweep_scratch_files.py
"""The scratch sweep (spec R-2) deletes only copies no live proposal references."""

from io import StringIO
from pathlib import Path

import pytest
from django.core.files.base import ContentFile
from django.core.management import call_command

from ifc_processor.tests.factories import IFCFileFactory
from writeback.models import ModificationProposal
from writeback.tests.factories import ModificationProposalFactory


def _scratch(ifc_file, tag: str) -> Path:
    original = Path(ifc_file.file.path)
    scratch = original.with_name(f".{original.stem}.proposal-{tag}{original.suffix}")
    scratch.write_bytes(b"ISO-10303-21;")
    return scratch


@pytest.mark.django_db
def test_sweep_keeps_pending_and_claimed_copies_and_removes_the_rest():
    """A PENDING and an APPROVED (mid-approval) row keep their copy; orphans and settled rows lose theirs."""
    ifc_file = IFCFileFactory()
    ifc_file.file.save("house.ifc", ContentFile(b"ISO-10303-21;"), save=True)
    kept = {
        status: _scratch(ifc_file, status)
        for status in (ModificationProposal.Status.PENDING, ModificationProposal.Status.APPROVED)
    }
    dropped = {
        status: _scratch(ifc_file, status)
        for status in (ModificationProposal.Status.APPLIED, ModificationProposal.Status.REJECTED)
    }
    for status, scratch in {**kept, **dropped}.items():
        ModificationProposalFactory(ifc_file=ifc_file, status=status, scratch_path=str(scratch))
    orphan = _scratch(ifc_file, "orphan")

    out = StringIO()
    call_command("sweep_scratch_files", stdout=out)

    assert all(path.exists() for path in kept.values())
    assert not any(path.exists() for path in dropped.values())
    assert not orphan.exists()
    assert "Removed 3 scratch file(s); 2 pending kept." in out.getvalue()
