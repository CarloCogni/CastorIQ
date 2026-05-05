# core/management/commands/provision_sample_project.py
"""
Auto-provision the shared Castor sample project for a freshly approved user.

Wired into the M3.6 admin Approve action so the user lands on a working
project from second one — no empty state, no friction. Idempotent: re-running
on a user who already has the sample project is a no-op.

Usage:
    cd src && uv run python manage.py provision_sample_project <user_id>
    cd src && uv run python manage.py provision_sample_project <user_id> --skip-pipeline

The fixture layout is documented in fixtures/sample-project/README.md and
fixtures/sample-project/PROVENANCE.md. If the IFC or PDFs are missing, the
command fails loud with the expected paths so the operator can drop them in.
"""

from __future__ import annotations

import logging
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files import File
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

logger = logging.getLogger(__name__)
User = get_user_model()

SAMPLE_PROJECT_NAME = "Sample Project (BuildingSMART Duplex)"
SAMPLE_PROJECT_DESCRIPTION = (
    "Auto-provisioned demo project. Curated IFC + a few specs so you can "
    "exercise Ask and Modify against something real on first login."
)


def _fixtures_root() -> Path:
    return Path(settings.BASE_DIR) / "fixtures" / "sample-project"


def _expected_ifc() -> Path:
    return _fixtures_root() / "building.ifc"


def _expected_docs_dir() -> Path:
    return _fixtures_root() / "docs"


class Command(BaseCommand):
    help = "Provision the shared Castor sample project for the given user (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument("user_id", help="User PK or username to provision.")
        parser.add_argument(
            "--skip-pipeline",
            action="store_true",
            help="Create rows + copy files, but skip IFC parsing and embeddings (fast).",
        )

    def handle(self, *args, **options):
        from environments.models import Project
        from environments.services.access_service import ProjectAccessService

        user = self._resolve_user(options["user_id"])
        skip_pipeline = options["skip_pipeline"]

        ifc_path = _expected_ifc()
        if not ifc_path.exists():
            raise CommandError(
                f"Sample IFC missing — expected file at {ifc_path}. "
                "See fixtures/sample-project/PROVENANCE.md for download instructions."
            )

        existing = Project.objects.filter(owner=user, name=SAMPLE_PROJECT_NAME).first()
        if existing:
            self.stdout.write(
                self.style.SUCCESS(
                    f"User '{user.username}' already has the sample project (id={existing.id}). "
                    "Nothing to do."
                )
            )
            return

        with transaction.atomic():
            project = Project.objects.create(
                name=SAMPLE_PROJECT_NAME,
                description=SAMPLE_PROJECT_DESCRIPTION,
                owner=user,
            )
            ProjectAccessService.bootstrap_owner_membership(project)

        self.stdout.write(
            self.style.SUCCESS(f"Created project '{project.name}' (id={project.id}).")
        )

        self._provision_ifc(project, ifc_path, skip_pipeline=skip_pipeline)
        self._provision_documents(project, _expected_docs_dir(), skip_pipeline=skip_pipeline)

        self.stdout.write(
            self.style.SUCCESS(
                f"\nSample project ready for {user.username}. "
                f"Visit /projects/{project.id}/ to open it."
            )
        )

    # ------------------------------------------------------------------

    def _resolve_user(self, identifier: str):
        # Try PK first (numeric), fall back to username.
        try:
            if identifier.isdigit():
                return User.objects.get(pk=int(identifier))
        except User.DoesNotExist:
            pass
        try:
            return User.objects.get(username=identifier)
        except User.DoesNotExist as exc:
            raise CommandError(f"User '{identifier}' not found.") from exc

    def _provision_ifc(self, project, ifc_path: Path, *, skip_pipeline: bool) -> None:
        from ifc_processor.models import IFCFile

        with ifc_path.open("rb") as fh:
            ifc_file = IFCFile.objects.create(
                project=project,
                name=ifc_path.name,
                file=File(fh, name=ifc_path.name),
                status=IFCFile.Status.PENDING,
            )
        self.stdout.write(f"  + IFCFile {ifc_file.name} ({ifc_file.id})")

        if skip_pipeline:
            self.stdout.write("    (skipped IFC pipeline — pass without --skip-pipeline to run it)")
            return

        try:
            from ifc_processor.services.processor import IFCProcessingService

            success = IFCProcessingService(ifc_file).run_pipeline()
            if success:
                self.stdout.write(
                    self.style.SUCCESS(f"    pipeline OK ({ifc_file.entities.count()} entities)")
                )
            else:
                self.stdout.write(self.style.WARNING("    pipeline reported failure; check logs"))
        except Exception as exc:
            logger.exception("Sample-project IFC pipeline failed: %s", exc)
            self.stdout.write(
                self.style.WARNING(f"    pipeline error: {type(exc).__name__}: {exc}")
            )

    def _provision_documents(self, project, docs_dir: Path, *, skip_pipeline: bool) -> None:
        from documents.models import Document

        if not docs_dir.exists():
            self.stdout.write(
                self.style.WARNING(f"  (no docs dir at {docs_dir} — skipping seed documents)")
            )
            return

        candidates = sorted(
            list(docs_dir.glob("*.pdf"))
            + list(docs_dir.glob("*.PDF"))
            + list(docs_dir.glob("*.txt"))
        )
        if not candidates:
            self.stdout.write(self.style.WARNING(f"  (docs dir is empty: {docs_dir})"))
            return

        for path in candidates:
            ext = path.suffix.lower()
            if ext == ".pdf":
                doc_type = Document.DocumentType.PDF
            elif ext == ".txt":
                doc_type = Document.DocumentType.TXT
            else:
                doc_type = Document.DocumentType.OTHER

            with path.open("rb") as fh:
                doc = Document.objects.create(
                    project=project,
                    name=path.name,
                    file=File(fh, name=path.name),
                    document_type=doc_type,
                    status=Document.Status.PENDING,
                )
            self.stdout.write(f"  + Document {doc.name} ({doc.id})")

            if skip_pipeline:
                continue

            try:
                from documents.services.document_processor import DocumentProcessor

                DocumentProcessor(doc).process()
            except Exception as exc:
                logger.exception("Sample-project document pipeline failed for %s: %s", path, exc)
                self.stdout.write(
                    self.style.WARNING(f"    pipeline error: {type(exc).__name__}: {exc}")
                )
