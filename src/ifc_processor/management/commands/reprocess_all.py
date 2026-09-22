# ifc_processor/management/commands/reprocess_all.py
"""Re-parse and re-embed every completed IFC file across all projects.

The one-shot fleet command for rolling out parser/description changes: it
runs the full ``IFCProcessingService`` pipeline (hash → parse → embeddings)
on every completed file, for every user's projects. Each file is processed
in its own transaction, so the run can be interrupted and re-launched safely
— finished files stay finished, the rest just run again.

Usage::

    # Everything, everywhere (the normal rollout invocation):
    cd src && uv run manage.py reprocess_all

    # Production (long-running — launch inside tmux/screen):
    docker compose exec web python manage.py reprocess_all

    # Preview / scope down:
    cd src && uv run manage.py reprocess_all --dry-run
    cd src && uv run manage.py reprocess_all --project <uuid-or-name>

Requires the embedding service (Ollama) to be reachable — checked once up
front so a dead service fails fast instead of failing per file.
"""

from __future__ import annotations

import logging
import time

from django.core.management.base import BaseCommand, CommandError

from ifc_processor.models import IFCFile

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Re-parse and re-embed every completed IFC file (all projects, all users). "
        "Safe to interrupt and re-launch; per-file atomic."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--project",
            default="",
            help="Limit to one project (UUID or name). Default: all projects.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="List the files that would be reprocessed and exit.",
        )

    def handle(self, *args, **options):
        files = self._target_files(options["project"])
        total = files.count()
        if total == 0:
            self.stdout.write("No completed IFC files found. Nothing to do.")
            return

        self.stdout.write(f"{total} completed IFC file(s) to reprocess.")

        if options["dry_run"]:
            for ifc_file in files:
                self.stdout.write(
                    f"  would reprocess: {self._label(ifc_file)} ({ifc_file.entity_count} entities)"
                )
            return

        self._check_embedding_service()

        processed, skipped, failed = 0, [], []
        for index, ifc_file in enumerate(files, start=1):
            label = self._label(ifc_file)

            if not ifc_file.file or not ifc_file.file.storage.exists(ifc_file.file.name):
                self.stdout.write(
                    self.style.WARNING(
                        f"[{index}/{total}] SKIP {label} — file missing from storage"
                    )
                )
                skipped.append(label)
                continue

            started = time.monotonic()
            try:
                from ifc_processor.services.processor import IFCProcessingService

                ok = IFCProcessingService(ifc_file).run_pipeline()
            except Exception as exc:
                logger.exception("reprocess_all: pipeline crashed for %s", label)
                ok, exc_note = False, f" ({type(exc).__name__}: {exc})"
            else:
                exc_note = ""

            duration = time.monotonic() - started
            if ok:
                processed += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f"[{index}/{total}] OK   {label} — "
                        f"{ifc_file.entities.count()} entities in {duration:.0f}s"
                    )
                )
            else:
                failed.append(label)
                self.stdout.write(
                    self.style.ERROR(f"[{index}/{total}] FAIL {label}{exc_note} — see logs")
                )

        self.stdout.write(
            f"\nDone: {processed} reprocessed, {len(skipped)} skipped, {len(failed)} failed."
        )
        for label in skipped:
            self.stdout.write(self.style.WARNING(f"  skipped: {label}"))
        for label in failed:
            self.stdout.write(self.style.ERROR(f"  failed:  {label}"))
        if failed:
            raise CommandError(f"{len(failed)} file(s) failed — re-launch after fixing the cause.")

    # ── Helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _label(ifc_file: IFCFile) -> str:
        project = ifc_file.project
        owner = getattr(project.owner, "username", "?")
        return f"{owner} / {project.name} / {ifc_file.name}"

    def _target_files(self, project_ident: str):
        files = (
            IFCFile.objects.filter(status=IFCFile.Status.COMPLETED)
            .select_related("project", "project__owner")
            .order_by("project__owner__username", "project__name", "name")
        )
        if not project_ident:
            return files

        from django.core.exceptions import ValidationError

        from environments.models import Project

        try:
            project = Project.objects.get(id=project_ident)
        except (Project.DoesNotExist, ValueError, ValidationError):
            try:
                project = Project.objects.get(name__iexact=project_ident)
            except Project.DoesNotExist:
                raise CommandError(f"Project not found: {project_ident!r}")
            except Project.MultipleObjectsReturned:
                raise CommandError(f"Multiple projects match {project_ident!r} — pass a UUID.")
        return files.filter(project=project)

    def _check_embedding_service(self) -> None:
        """Fail fast when Ollama is down instead of failing on every file."""
        from embeddings.services.embedding_service import EmbeddingService

        try:
            vector = EmbeddingService().embed_query("connectivity check")
        except Exception as exc:
            raise CommandError(f"Embedding service unreachable: {exc}")
        if not vector:
            raise CommandError(
                "Embedding service returned no vector — is Ollama running with "
                "the embedding model pulled?"
            )
