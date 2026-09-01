# writeback/management/commands/benchmark_rav.py
"""Score the conflict scanner (RAV) against the planted-conflict corpus.

Runs ``ConflictScanService`` for real — real embeddings, real model — on a
project that holds ``Ifc4_SampleHouse.ifc`` and the three documents under
``fixtures/benchmark/rav/docs/``, then scores every finding against
``key.json``: precision, recall by severity, and how many aligned requirements
were correctly left alone.

Usage::

    # One-time: upload + process the corpus documents into the project.
    cd src && uv run manage.py benchmark_rav --project <uuid> --setup

    # Default settings, saved for later comparison.
    cd src && uv run manage.py benchmark_rav --project <uuid> --json ../runs/rav.json

    # Ablation sweep: production vs each mitigation switched off.
    cd src && uv run manage.py benchmark_rav --project <uuid> --ablate

    # One variant by hand.
    cd src && uv run manage.py benchmark_rav --project <uuid> --no-type-gate --confidence 0.5

    # Regression check.
    cd src && uv run manage.py benchmark_rav --project <uuid> --baseline ../runs/rav.json

Conflicts the run creates are deleted afterwards unless ``--keep-conflicts``.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from environments.models import Project
from writeback.services.benchmark.rav import (
    RavCorpusError,
    RavReport,
    RavRunner,
    ScanSettings,
    diff_rav_runs,
    load_key,
    render_rav_report,
)
from writeback.services.benchmark.rav.report import load_baseline, write_json

logger = logging.getLogger(__name__)

RAV_ROOT = Path(__file__).resolve().parents[4] / "fixtures/benchmark/rav"
DEFAULT_KEY = RAV_ROOT / "key.json"
DEFAULT_DOCS = RAV_ROOT / "docs"

ABLATION_VARIANTS = (
    ScanSettings(),
    ScanSettings(type_gate=False),
    ScanSettings(keyword_filter=False),
    ScanSettings(confidence_threshold=0.0),
    ScanSettings(type_gate=False, keyword_filter=False, confidence_threshold=0.0),
)


class Command(BaseCommand):
    help = "Run the conflict scanner on the planted corpus and score precision / recall."

    def add_arguments(self, parser):
        parser.add_argument("--project", required=True, help="Project UUID or name.")
        parser.add_argument(
            "--key", default=str(DEFAULT_KEY), help=f"Key file. Default: {DEFAULT_KEY}"
        )
        parser.add_argument(
            "--setup",
            action="store_true",
            help=f"Upload and process the PDFs in {DEFAULT_DOCS} into the project, then exit.",
        )
        parser.add_argument("--ablate", action="store_true", help="Run the full ablation sweep.")
        parser.add_argument("--no-type-gate", action="store_true")
        parser.add_argument("--no-keyword-filter", action="store_true")
        parser.add_argument(
            "--confidence", type=float, default=None, help="Confidence cut (default 0.7)."
        )
        parser.add_argument(
            "--distance", type=float, default=None, help="Entity relevance cosine cut."
        )
        parser.add_argument(
            "--top-k", type=int, default=None, help="Entities per requirement chunk."
        )
        parser.add_argument(
            "--all-types", action="store_true", help="Do not skip low-value IFC types."
        )
        parser.add_argument("--json", default="", help="Write the run artifact here.")
        parser.add_argument("--baseline", default="", help="Diff against a previous artifact.")
        parser.add_argument("--keep-conflicts", action="store_true")
        parser.add_argument("--verbose-cases", action="store_true", help="List passing cases too.")

    def handle(self, *args, **options):
        project = self._resolve_project(options["project"])

        if options["setup"]:
            self._setup_documents(project)
            return

        try:
            corpus = load_key(options["key"])
        except RavCorpusError as e:
            raise CommandError(str(e)) from e

        self._check_documents(project, corpus)

        variants = list(ABLATION_VARIANTS) if options["ablate"] else [self._settings_from(options)]
        runner = RavRunner(project, keep_conflicts=options["keep_conflicts"])

        self.stdout.write(
            f"RAV benchmark: {len(corpus.cases)} cases, {corpus.triples()} entity-property "
            f"triples, {len(corpus.conflict_cases)} planted conflicts, "
            f"{len(corpus.negative_cases)} aligned requirements"
        )

        reports: list[RavReport] = []
        for settings in variants:
            self.stdout.write(f"  scanning [{settings.label()}] …")
            sheet, stats = runner.run(corpus, settings)
            reports.append(
                RavReport(
                    settings=settings,
                    sheet=sheet,
                    run_stats=stats,
                    started_at=datetime.now(UTC).isoformat(timespec="seconds"),
                )
            )

        self.stdout.write(render_rav_report(reports, verbose=options["verbose_cases"]))

        if options["baseline"]:
            baseline = load_baseline(options["baseline"])
            for report in reports:
                self.stdout.write(diff_rav_runs(baseline, report))

        if options["json"]:
            path = write_json(reports, options["json"])
            self.stdout.write(f"\nartifact written: {path}")

    # ── Helpers ────────────────────────────────────────────

    @staticmethod
    def _settings_from(options) -> ScanSettings:
        return ScanSettings(
            confidence_threshold=(
                0.7 if options["confidence"] is None else float(options["confidence"])
            ),
            type_gate=not options["no_type_gate"],
            keyword_filter=not options["no_keyword_filter"],
            entity_relevance_threshold=options["distance"],
            entity_top_k=options["top_k"],
            skip_low_value=not options["all_types"],
        )

    @staticmethod
    def _resolve_project(identifier: str) -> Project:
        try:
            return Project.objects.get(id=identifier)
        except (Project.DoesNotExist, ValidationError, ValueError):
            pass
        match = Project.objects.filter(name=identifier).first()
        if match is None:
            raise CommandError(f"No project with id or name {identifier!r}.")
        return match

    def _check_documents(self, project: Project, corpus) -> None:
        """Refuse to score against a project missing any corpus document."""
        from documents.models import Document

        names = {
            d.name.rsplit(".", 1)[0]
            for d in Document.objects.filter(project=project, status="completed")
        }
        missing = [doc for doc in corpus.documents if doc not in names]
        if missing:
            raise CommandError(
                f"Project has no processed document for: {', '.join(missing)}. "
                "Run with --setup first."
            )

    def _setup_documents(self, project: Project) -> None:
        """Upload the corpus PDFs into the project and run the document pipeline."""
        from django.core.files import File

        from documents.models import Document
        from documents.services.document_processor import DocumentProcessor

        pdfs = sorted(DEFAULT_DOCS.glob("*.pdf"))
        if not pdfs:
            raise CommandError(
                f"No PDFs under {DEFAULT_DOCS}. Render them first: "
                "uv run python fixtures/benchmark/rav/render_pdfs.py"
            )

        for path in pdfs:
            existing = Document.objects.filter(project=project, name=path.name).first()
            if existing and existing.status == Document.Status.COMPLETED:
                self.stdout.write(f"  = {path.name} already processed")
                continue
            if existing:
                existing.delete()
            with path.open("rb") as fh:
                doc = Document.objects.create(
                    project=project,
                    name=path.name,
                    file=File(fh, name=path.name),
                    document_type=Document.DocumentType.PDF,
                    status=Document.Status.PENDING,
                )
            self.stdout.write(f"  + {path.name} … processing")
            DocumentProcessor(doc).process()
            doc.refresh_from_db()
            self.stdout.write(f"    status: {doc.status}")
