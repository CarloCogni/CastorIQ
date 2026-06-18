# core/management/commands/run_sample_pipeline.py
"""
Run the (slow) processing pipeline for a user's already-provisioned Sample
Project files.

This is the deferred half of sample-project provisioning. ``provision_sample_project``
(invoked from the user-creation signal with ``--skip-pipeline``) creates the
Project, copies the fixture files, and leaves the IFCFile / Document rows in the
``PENDING`` state — all sub-second, safe to run inside the HTTP request that
created the user. This command does the expensive part — IFC parse + Ollama
embeddings for each model, PDF parse + embeddings for each doc — which is far too
slow to run in-request (it blows past nginx's ``proxy_read_timeout`` and returns a
504). The user-creation signal hands this off to a daemon thread; an operator can
also run it by hand to recover a project whose files are stuck ``PENDING``/``FAILED``.

Idempotent: only ``PENDING`` and ``FAILED`` rows are (re)processed; ``COMPLETED``
rows are left untouched. Safe to re-run any number of times.

Usage:
    cd src && uv run python manage.py run_sample_pipeline <user_id>
"""

from __future__ import annotations

import logging

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from .provision_sample_project import SAMPLE_PROJECT_NAME

logger = logging.getLogger(__name__)
User = get_user_model()


class Command(BaseCommand):
    help = "Process PENDING/FAILED files in a user's Sample Project (idempotent)."

    def add_arguments(self, parser) -> None:
        parser.add_argument("user_id", help="User PK or username whose Sample Project to process.")

    def handle(self, *args, **options) -> None:
        from documents.services.document_processor import DocumentProcessor
        from environments.models import Project
        from ifc_processor.models import IFCFile
        from ifc_processor.services.processor import IFCProcessingService

        user = self._resolve_user(options["user_id"])

        project = Project.objects.filter(owner=user, name=SAMPLE_PROJECT_NAME).first()
        if project is None:
            raise CommandError(
                f"User '{user.username}' has no '{SAMPLE_PROJECT_NAME}'. "
                "Run provision_sample_project first."
            )

        unprocessed = (IFCFile.Status.PENDING, IFCFile.Status.FAILED)

        ifc_files = list(project.ifc_files.filter(status__in=unprocessed))
        documents = list(project.documents.filter(status__in=unprocessed))

        if not ifc_files and not documents:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Nothing to do — no PENDING/FAILED files in Sample Project "
                    f"(id={project.id}) for '{user.username}'."
                )
            )
            return

        for ifc_file in ifc_files:
            self._process_ifc(IFCProcessingService, ifc_file)

        for document in documents:
            self._process_document(DocumentProcessor, document)

        self.stdout.write(
            self.style.SUCCESS(
                f"Sample-project pipeline finished for '{user.username}' "
                f"(id={project.id}): {len(ifc_files)} IFC file(s), {len(documents)} document(s)."
            )
        )

    # ------------------------------------------------------------------

    def _resolve_user(self, identifier: str):
        # PK first (numeric), fall back to username — mirrors provision_sample_project.
        if identifier.isdigit():
            try:
                return User.objects.get(pk=int(identifier))
            except User.DoesNotExist:
                pass
        try:
            return User.objects.get(username=identifier)
        except User.DoesNotExist as exc:
            raise CommandError(f"User '{identifier}' not found.") from exc

    def _process_ifc(self, service_cls, ifc_file) -> None:
        # Per-file try/except: one model's failure must not abort the rest. The
        # service records the failure on the row (status=FAILED, error_message),
        # so a later re-run picks it back up.
        try:
            success = service_cls(ifc_file).run_pipeline()
        except Exception as exc:
            logger.exception("Sample-project IFC pipeline failed for %s: %s", ifc_file.name, exc)
            self.stdout.write(
                self.style.WARNING(f"  ✗ {ifc_file.name}: {type(exc).__name__}: {exc}")
            )
            return
        if success:
            self.stdout.write(self.style.SUCCESS(f"  ✓ {ifc_file.name}"))
        else:
            self.stdout.write(self.style.WARNING(f"  ✗ {ifc_file.name}: pipeline reported failure"))

    def _process_document(self, processor_cls, document) -> None:
        try:
            success = processor_cls(document).process()
        except Exception as exc:
            logger.exception(
                "Sample-project document pipeline failed for %s: %s", document.name, exc
            )
            self.stdout.write(
                self.style.WARNING(f"  ✗ {document.name}: {type(exc).__name__}: {exc}")
            )
            return
        if success:
            self.stdout.write(self.style.SUCCESS(f"  ✓ {document.name}"))
        else:
            self.stdout.write(self.style.WARNING(f"  ✗ {document.name}: pipeline reported failure"))
