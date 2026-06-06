# documents/management/commands/check_document_files.py
"""
Walk Document rows and report those whose file is missing from storage.

A Document row can outlive its file when a redeploy wipes the media volume,
when an upload completed at the DB layer but failed at the storage layer,
or when a file was deleted out-of-band. Nginx serves /media/ directly, so
the user just sees a 404 — the orphan is invisible until someone clicks.

Usage:
    uv run manage.py check_document_files                    # report-only, all projects
    uv run manage.py check_document_files --project <uuid>   # scope to one project
    uv run manage.py check_document_files --json             # machine-readable output
    uv run manage.py check_document_files --delete-orphans   # destructive; deletes orphan rows

Exit code: 0 if no orphans, 1 if orphans were found (composes with `&&` in cron alerts).
"""

from __future__ import annotations

import json
import logging
import sys

from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandParser

from documents.models import Document

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Report Document rows whose file is missing from storage."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--project",
            type=str,
            default=None,
            help="Restrict the scan to one project UUID.",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            help="Emit one JSON object per orphan to stdout, plus a final summary object.",
        )
        parser.add_argument(
            "--delete-orphans",
            action="store_true",
            help="Destructive: delete orphan Document rows after reporting them.",
        )

    def handle(self, *args, **options) -> None:
        project_id: str | None = options["project"]
        as_json: bool = options["json"]
        delete_orphans: bool = options["delete_orphans"]

        qs = Document.objects.all()
        if project_id:
            qs = qs.filter(project_id=project_id)

        orphans: list[Document] = []
        for doc in qs.iterator():
            if not doc.file or not doc.file.name:
                # No file path recorded — treat as orphan so it surfaces.
                orphans.append(doc)
                continue
            if not default_storage.exists(doc.file.name):
                orphans.append(doc)

        for doc in orphans:
            self._emit(doc, as_json=as_json)

        if as_json:
            self.stdout.write(
                json.dumps(
                    {
                        "summary": True,
                        "orphan_count": len(orphans),
                        "project_id": project_id,
                    }
                )
            )
        else:
            if not orphans:
                self.stdout.write(self.style.SUCCESS("No orphan documents found."))
            else:
                self.stdout.write(self.style.WARNING(f"\nFound {len(orphans)} orphan document(s)."))

        if delete_orphans and orphans:
            ids = [doc.id for doc in orphans]
            deleted, _ = Document.objects.filter(id__in=ids).delete()
            msg = f"Deleted {deleted} orphan Document row(s)."
            logger.warning(msg)
            if as_json:
                self.stdout.write(json.dumps({"deleted": deleted}))
            else:
                self.stdout.write(self.style.SUCCESS(msg))

        if orphans:
            sys.exit(1)

    def _emit(self, doc: Document, *, as_json: bool) -> None:
        if as_json:
            self.stdout.write(
                json.dumps(
                    {
                        "project_id": str(doc.project_id),
                        "document_id": str(doc.id),
                        "name": doc.name,
                        "file": doc.file.name if doc.file else None,
                        "status": doc.status,
                        "created_at": doc.created_at.isoformat()
                        if getattr(doc, "created_at", None)
                        else None,
                    }
                )
            )
            return

        self.stdout.write(
            f"  project={doc.project_id} doc={doc.id} "
            f"name={doc.name!r} file={(doc.file.name if doc.file else None)!r} "
            f"status={doc.status}"
        )
