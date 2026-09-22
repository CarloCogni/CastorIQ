# chat/management/commands/benchmark_ask.py
"""Score the Ask (RAG) pipeline on real fixture models and real questions.

Nothing is mocked: fixtures are parsed by the real IFC pipeline (Ollama
embeddings included), questions run through ``RAGService.generate_answer``
against the live LLM, and answers are scored against ground truth computed
independently with IfcOpenShell from the fixture file itself.

Usage::

    # Full run over all fixtures (parses them on first use — slow once).
    cd src && uv run manage.py benchmark_ask

    # One fixture, artifact saved for later comparison.
    cd src && uv run manage.py benchmark_ask --fixture duplex --json ../runs/ask-today.json

Benchmark projects are created per fixture (named "[bench-ask] <fixture>",
owned by --user or the first superuser) and reused across runs.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.files import File
from django.core.management.base import BaseCommand, CommandError

from chat.models import ChatSession
from chat.services.ask_benchmark import (
    CASES,
    FIXTURES,
    AskBenchmarkReport,
    compute_ground_truth,
    render_report,
    resolve_fixture,
    score_case,
)

logger = logging.getLogger(__name__)
User = get_user_model()

PROJECT_NAME_TEMPLATE = "[bench-ask] {fixture}"


class Command(BaseCommand):
    help = "Run the Ask question corpus against fixture models and score the answers."

    def add_arguments(self, parser):
        parser.add_argument(
            "--fixture",
            action="append",
            default=[],
            choices=sorted(FIXTURES),
            help="Fixture to run (repeatable). Default: all.",
        )
        parser.add_argument(
            "--user",
            default="",
            help="Username owning the benchmark projects. Default: first superuser.",
        )
        parser.add_argument(
            "--json", default="", help="Write run artifacts here (one per fixture)."
        )
        parser.add_argument(
            "--tier",
            type=int,
            choices=(1, 2),
            default=0,
            help="Run only one tier of cases. Default: both.",
        )

    def handle(self, *args, **options):
        user = self._resolve_user(options["user"])
        fixture_names = options["fixture"] or sorted(FIXTURES)
        cases = [c for c in CASES if not options["tier"] or c.tier == options["tier"]]

        reports: list[AskBenchmarkReport] = []
        for fixture_name in fixture_names:
            reports.append(self._run_fixture(fixture_name, user, cases))

        self.stdout.write(render_report(reports))

        if options["json"]:
            base = Path(options["json"])
            for report in reports:
                path = base.with_name(f"{base.stem}.{report.fixture}{base.suffix or '.json'}")
                report.write_json(path)
                self.stdout.write(f"wrote {path}")

    # ── One fixture pass ───────────────────────────────────────────────

    def _run_fixture(self, fixture_name: str, user, cases) -> AskBenchmarkReport:
        from chat.services.rag_service import RAGService

        try:
            path = resolve_fixture(fixture_name)
        except FileNotFoundError as exc:
            raise CommandError(str(exc))

        ground = compute_ground_truth(path)
        project = self._ensure_project(fixture_name, user, path)
        session = ChatSession.objects.create(
            project=project,
            user=user,
            mode=ChatSession.Mode.ASK,
            title=f"benchmark {datetime.now(UTC):%Y-%m-%d %H:%M}",
        )

        self.stdout.write(
            self.style.MIGRATE_HEADING(f"\n=== {fixture_name} ({path.name}, {ground.schema}) ===")
        )
        rag = RAGService(user=user)
        report = AskBenchmarkReport(
            fixture=fixture_name,
            model_label=rag.model_name,
            started_at=datetime.now(UTC).isoformat(),
        )

        for index, case in enumerate(cases, start=1):
            started = time.monotonic()
            try:
                answer, _context, _utilization = rag.generate_answer(
                    project, session, case.text, scope="auto"
                )
            except Exception as exc:  # a crashed case is a failed case, not a dead run
                logger.exception("benchmark_ask case %s crashed", case.case_id)
                answer = f"[ERROR] {type(exc).__name__}: {exc}"
            result = score_case(case, answer, ground, latency_s=time.monotonic() - started)
            report.results.append(result)
            style = self.style.SUCCESS if result.passed or result.skipped else self.style.ERROR
            self.stdout.write(
                style(
                    f"  [{index:>2}/{len(cases)}] {result.mark} {case.case_id:<20} "
                    f"{result.latency_s:>5.1f}s"
                )
            )
        return report

    # ── Setup helpers ──────────────────────────────────────────────────

    def _resolve_user(self, username: str):
        if username:
            try:
                return User.objects.get(username=username)
            except User.DoesNotExist:
                raise CommandError(f"User {username!r} not found.")
        user = User.objects.filter(is_superuser=True).order_by("date_joined").first()
        if user is None:
            raise CommandError("No superuser found — pass --user <username>.")
        return user

    def _ensure_project(self, fixture_name: str, user, path: Path):
        """Get or create the benchmark project with a fully processed IFC file."""
        from environments.models import Project
        from environments.services.access_service import ProjectAccessService
        from ifc_processor.models import IFCFile

        name = PROJECT_NAME_TEMPLATE.format(fixture=fixture_name)
        project = Project.objects.filter(owner=user, name=name).first()
        if project is None:
            project = Project.objects.create(
                name=name,
                description=f"Ask benchmark project for fixture {path.name}. Safe to delete.",
                owner=user,
            )
            ProjectAccessService.bootstrap_owner_membership(project)
            self.stdout.write(f"created project {name!r}")

        ifc_file = (
            IFCFile.objects.filter(project=project, status=IFCFile.Status.COMPLETED)
            .order_by("-created_at")
            .first()
        )
        if ifc_file is not None and Path(ifc_file.file.path).exists():
            return project

        self.stdout.write(f"parsing {path.name} (first run for this fixture — this is slow)...")
        with path.open("rb") as fh:
            ifc_file = IFCFile.objects.create(
                project=project,
                name=path.name,
                file=File(fh, name=path.name),
                status=IFCFile.Status.PENDING,
            )

        from ifc_processor.services.processor import IFCProcessingService

        if not IFCProcessingService(ifc_file).run_pipeline():
            raise CommandError(
                f"IFC pipeline failed for {path.name} — check logs (is Ollama running?)."
            )
        self.stdout.write(f"parsed {path.name}: {ifc_file.entities.count()} entities")
        return project
