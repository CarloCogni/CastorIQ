# ifc_processor/management/commands/time_snapshot.py
"""Measure how long the largest processed IFC file takes to open and snapshot.

Writeback V3 (spec P-1) requires these two numbers to be measured before any
optimisation is considered. Read-only; prints one line per measurement.
"""

from __future__ import annotations

import time

import ifcopenshell
from django.core.management.base import BaseCommand, CommandError

from ifc_processor.models import IFCFile
from ifc_processor.services.ifc_diff import IfcSnapshot


class Command(BaseCommand):
    help = (
        "Time ifcopenshell.open() and IfcSnapshot.from_model() on the largest processed IFC file."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument("--file", help="IFCFile id; defaults to the largest completed file")

    def handle(self, *args, **options) -> None:
        ifc_file = self._pick(options.get("file"))
        path = ifc_file.file.path

        started = time.perf_counter()
        model = ifcopenshell.open(path)
        open_s = time.perf_counter() - started

        started = time.perf_counter()
        snapshot = IfcSnapshot.from_model(model, path=path)
        snapshot_s = time.perf_counter() - started

        self.stdout.write(f"file: {ifc_file.name} ({ifc_file.file.size / 1_048_576:.1f} MiB)")
        self.stdout.write(f"entities: {snapshot.entity_total}")
        self.stdout.write(f"open: {open_s:.2f}s")
        self.stdout.write(f"snapshot: {snapshot_s:.2f}s")

    @staticmethod
    def _pick(file_id: str | None) -> IFCFile:
        files = IFCFile.objects.filter(status="completed")
        if file_id:
            files = files.filter(pk=file_id)
        candidates = [f for f in files if f.file]
        if not candidates:
            raise CommandError("No completed IFC file with a stored file was found.")
        return max(candidates, key=lambda f: f.file.size)
