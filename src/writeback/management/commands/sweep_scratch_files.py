# writeback/management/commands/sweep_scratch_files.py
"""Delete scratch copies whose proposal is no longer pending (spec R-2).

Apply, reject and supersede delete their own scratch file; this command is for
crashes only. It also removes orphan ``.<stem>.proposal-*.ifc`` files that no
pending or claimed (approved, mid-swap) proposal references.
"""

from __future__ import annotations

from pathlib import Path

from django.core.management.base import BaseCommand

from ifc_processor.models import IFCFile
from writeback.models import ModificationProposal


class Command(BaseCommand):
    help = "Delete scratch IFC copies left behind by proposals that are no longer pending."

    def handle(self, *args, **options) -> None:
        # A claimed (APPROVED) row is mid-approval: its copy is about to be
        # swapped in and must survive a sweep that runs in that window.
        pending = set(
            ModificationProposal.objects.filter(
                status__in=(
                    ModificationProposal.Status.PENDING,
                    ModificationProposal.Status.APPROVED,
                )
            )
            .exclude(scratch_path="")
            .values_list("scratch_path", flat=True)
        )
        removed = 0
        for ifc_file in IFCFile.objects.exclude(file=""):
            folder = Path(ifc_file.file.path).parent
            for scratch in folder.glob(".*.proposal-*.ifc"):
                if str(scratch) in pending:
                    continue
                scratch.unlink(missing_ok=True)
                removed += 1
        self.stdout.write(f"Removed {removed} scratch file(s); {len(pending)} pending kept.")
