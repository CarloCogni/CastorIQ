# core/management/commands/provision_sample_project.py
"""
Auto-provision the shared Castor sample project for a freshly approved user.

Wired into the M3.6 admin Approve action so the user lands on a working
project from second one — no empty state, no friction. Idempotent: re-running
on a user who already has the sample project is a no-op.

Usage:
    cd src && uv run python manage.py provision_sample_project <user_id>
    cd src && uv run python manage.py provision_sample_project <user_id> --skip-pipeline
    cd src && uv run python manage.py provision_sample_project <user_id> --force-files

The fixture layout is documented in fixtures/sample-project/README.md and
fixtures/sample-project/PROVENANCE.md. The command provisions BOTH the
architectural and structural IFC files into the same Project so Ask can
cross-cut between disciplines. If any expected fixture is missing, the
command fails loud with all missing paths listed so the operator can
drop them in.

`--force-files` repairs an existing Sample Project whose files are missing
from MEDIA_ROOT (e.g. after a media_volume reset). Top-up semantics: rows
whose underlying files still exist on disk are left alone; rows whose files
are missing get the fixture re-copied into storage; missing rows are created
from the fixture. Never deletes existing rows.
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

SAMPLE_PROJECT_NAME = "Sample Project"
SAMPLE_PROJECT_DESCRIPTION = (
    "Auto-provisioned demo project. Architectural + structural IFC plus a few "
    "specs so you can exercise Ask and Modify against something real on first login."
)

# Filenames the operator drops into fixtures/sample-project/. Each (filename,
# label) becomes its own IFCFile row on the same Project — Ask can then
# cross-cut between disciplines, and Modify routes per file.
_SAMPLE_IFCS: tuple[tuple[str, str], ...] = (
    ("architectural.ifc", "architectural model"),
    ("structural.ifc", "structural model"),
)


def _fixtures_root() -> Path:
    # BASE_DIR is <repo>/src (or /app/src in the container). Fixtures live at
    # the repo root in fixtures/sample-project/ — see the layout documented in
    # fixtures/sample-project/README.md and the Dockerfile's `COPY fixtures/
    # /app/fixtures/`.
    return Path(settings.BASE_DIR).parent / "fixtures" / "sample-project"


def _expected_ifc_paths() -> list[Path]:
    return [_fixtures_root() / name for name, _ in _SAMPLE_IFCS]


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
        parser.add_argument(
            "--force-files",
            action="store_true",
            help=(
                "If the Sample Project already exists, top up missing files: "
                "re-copy fixtures for rows whose file is gone from MEDIA_ROOT, "
                "and create rows for fixtures with no matching row. Never deletes."
            ),
        )

    def handle(self, *args, **options):
        from environments.models import Project
        from environments.services.access_service import ProjectAccessService

        user = self._resolve_user(options["user_id"])
        skip_pipeline = options["skip_pipeline"]
        force_files = options["force_files"]

        ifc_paths = _expected_ifc_paths()
        missing = [p for p in ifc_paths if not p.exists()]
        if missing:
            raise CommandError(
                "Sample IFC fixtures missing — expected files at:\n  "
                + "\n  ".join(str(p) for p in missing)
                + "\nSee fixtures/sample-project/PROVENANCE.md for sourcing instructions."
            )

        existing = Project.objects.filter(owner=user, name=SAMPLE_PROJECT_NAME).first()
        if existing and not force_files:
            self.stdout.write(
                self.style.SUCCESS(
                    f"User '{user.username}' already has the sample project (id={existing.id}). "
                    "Nothing to do. Pass --force-files to re-seed missing files."
                )
            )
            return

        if existing:
            project = existing
            self.stdout.write(
                self.style.SUCCESS(
                    f"Topping up existing sample project (id={project.id}) for '{user.username}'."
                )
            )
        else:
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

        for filename, label in _SAMPLE_IFCS:
            self._provision_ifc(
                project,
                _fixtures_root() / filename,
                label=label,
                skip_pipeline=skip_pipeline,
            )
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

    def _provision_ifc(self, project, ifc_path: Path, *, label: str, skip_pipeline: bool) -> None:
        from ifc_processor.models import IFCFile

        existing = IFCFile.objects.filter(project=project, name=ifc_path.name).first()
        if existing is not None:
            if existing.file and existing.file.storage.exists(existing.file.name):
                self.stdout.write(
                    f"  = IFCFile {existing.name} ({label}) ({existing.id}) — file on disk, kept"
                )
                return
            with ifc_path.open("rb") as fh:
                existing.file.save(ifc_path.name, File(fh, name=ifc_path.name), save=False)
            existing.status = IFCFile.Status.PENDING
            existing.save()
            ifc_file = existing
            self.stdout.write(f"  ↻ IFCFile {ifc_file.name} ({label}) ({ifc_file.id}) — re-seeded")
        else:
            with ifc_path.open("rb") as fh:
                ifc_file = IFCFile.objects.create(
                    project=project,
                    name=ifc_path.name,
                    file=File(fh, name=ifc_path.name),
                    status=IFCFile.Status.PENDING,
                )
            self.stdout.write(f"  + IFCFile {ifc_file.name} ({label}) ({ifc_file.id})")

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
            logger.exception("Sample-project IFC pipeline failed for %s: %s", ifc_path.name, exc)
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

            existing = Document.objects.filter(project=project, name=path.name).first()
            if existing is not None:
                if existing.file and existing.file.storage.exists(existing.file.name):
                    self.stdout.write(
                        f"  = Document {existing.name} ({existing.id}) — file on disk, kept"
                    )
                    continue
                with path.open("rb") as fh:
                    existing.file.save(path.name, File(fh, name=path.name), save=False)
                existing.status = Document.Status.PENDING
                existing.save()
                doc = existing
                self.stdout.write(f"  ↻ Document {doc.name} ({doc.id}) — re-seeded")
            else:
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
