# writeback/management/commands/benchmark_writeback.py
"""Score the writeback pipeline on a corpus of natural-language prompts.

Runs real prompts through the real pipeline against a real model, executes the
resulting journals on a throwaway copy of the project's IFC, and reports two
scores: how often the request was *understood* (routed as the corpus says it
should be) and how often the resulting journal was faithfully *written* to the
file.

Nothing here is a mock. That is the point — unit tests already cover the stages
with a stubbed LLM; this answers the question those cannot: does the system
handle real language, with this model, today.

Usage::

    # Understanding only — fast, no writes.
    cd src && uv run manage.py benchmark_writeback \\
        --project <uuid> --no-execute --filter 1

    # Full pass, saved for later comparison.
    cd src && uv run manage.py benchmark_writeback \\
        --project <uuid> --json ../runs/today.json

    # Compare models.
    cd src && uv run manage.py benchmark_writeback --project <uuid> \\
        --model ollama:qwen2.5-coder --model anthropic:claude-sonnet-5

    # Regression check against a previous run.
    cd src && uv run manage.py benchmark_writeback \\
        --project <uuid> --baseline ../runs/today.json

The project's own IFC file is never modified — see ``benchmark/runner.py``.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from environments.models import Project
from writeback.services.benchmark import (
    BenchmarkReport,
    BenchmarkRunner,
    CorpusError,
    diff_runs,
    parse_corpus,
    render_report,
)
from writeback.services.benchmark.report import load_baseline

logger = logging.getLogger(__name__)

DEFAULT_CORPUS = (
    Path(__file__).resolve().parents[4] / "fixtures/benchmark/pipeline-test-prompts.txt"
)


class Command(BaseCommand):
    help = (
        "Run the natural-language corpus through the writeback pipeline and "
        "score understanding (routing) and fidelity (did the write land)."
    )

    def add_arguments(self, parser):
        parser.add_argument("--project", required=True, help="Project UUID or name.")
        parser.add_argument(
            "--corpus",
            default=str(DEFAULT_CORPUS),
            help=f"Prompt corpus file. Default: {DEFAULT_CORPUS}",
        )
        parser.add_argument(
            "--model",
            action="append",
            default=[],
            metavar="PROVIDER:MODEL",
            help=(
                "Model to benchmark, e.g. 'ollama:qwen2.5-coder' or "
                "'anthropic:claude-sonnet-5'. Repeat to sweep. "
                "Omit to use the current SiteLLMConfig."
            ),
        )
        parser.add_argument(
            "--filter",
            default="",
            help="Comma-separated corpus section numbers to run, e.g. '1,2,12'.",
        )
        parser.add_argument(
            "--repeat",
            type=int,
            default=1,
            help="Run each case N times; a case passes only if every run passes.",
        )
        parser.add_argument("--json", default="", help="Write the run artifact here.")
        parser.add_argument("--baseline", default="", help="Diff against a previous artifact.")
        parser.add_argument(
            "--no-execute",
            action="store_true",
            help="Score routing only — skip journal execution and file verification.",
        )
        parser.add_argument(
            "--keep-failure-records",
            action="store_true",
            help=(
                "Keep the FailureRecords the run creates. By default they are "
                "deleted — ~a third of the corpus expects a rejection, so a run "
                "would otherwise flood the metacastor table with expected noise."
            ),
        )
        parser.add_argument("--verbose-slots", action="store_true", help="Report slot mismatches.")

    def handle(self, *args, **options):
        # Stamped before any case runs so the FailureRecord purge has a precise
        # lower bound — records that predate the run must survive it.
        self._run_started = datetime.now(UTC)

        project = self._resolve_project(options["project"])
        ifc_file = self._resolve_ifc_file(project)
        cases = self._load_cases(options["corpus"], options["filter"])
        repeats = max(1, options["repeat"])
        execute = not options["no_execute"]

        self.stdout.write(
            f"{len(cases)} case(s) from {Path(options['corpus']).name} "
            f"against project {project.name!r} / {Path(ifc_file.file.name).name}"
        )
        if not execute:
            self.stdout.write(self.style.WARNING("execution disabled — understanding score only"))

        targets = options["model"] or [""]
        reports = [
            self._run_one_model(target, project, ifc_file, cases, repeats=repeats, execute=execute)
            for target in targets
        ]

        self.stdout.write(render_report(reports, verbose=options["verbose_slots"]))

        if options["json"]:
            for report in reports:
                path = self._artifact_path(options["json"], report, len(reports))
                report.write_json(path)
                self.stdout.write(f"\nwrote {path}")

        if options["baseline"]:
            try:
                baseline = load_baseline(options["baseline"])
            except (OSError, ValueError) as e:
                raise CommandError(f"Could not read baseline {options['baseline']!r}: {e}")
            for report in reports:
                self.stdout.write(diff_runs(baseline, report))

        if not options["keep_failure_records"]:
            self._purge_failure_records(project)

        total_failures = sum(len(r.failures) + len(r.errored) for r in reports)
        if total_failures:
            self.stdout.write(
                self.style.WARNING(f"\n{total_failures} case(s) did not meet expectations.")
            )
        else:
            self.stdout.write(self.style.SUCCESS("\nAll scored cases met expectations."))

    # ── One model pass ─────────────────────────────────────

    def _run_one_model(
        self, target: str, project, ifc_file, cases, *, repeats: int, execute: bool
    ) -> BenchmarkReport:
        """Run the corpus once under `target`, restoring site config after."""
        label = target or self._current_model_label()
        started = datetime.now(UTC)

        restore = self._apply_model(target) if target else None
        try:
            runner = BenchmarkRunner(project, ifc_file=ifc_file, execute=execute)
            results = []
            self.stdout.write(self.style.MIGRATE_HEADING(f"\n=== {label} ==="))
            for index, case in enumerate(cases, start=1):
                result = runner.run_case(case)
                for _ in range(repeats - 1):
                    # Re-running is how LLM non-determinism is surfaced rather
                    # than averaged away: the worst run is the one reported.
                    retry = runner.run_case(case)
                    if not retry.passed:
                        result = retry
                        break
                results.append(result)
                self._write_progress(index, len(cases), result)
        finally:
            if restore:
                restore()

        report = BenchmarkReport(
            model_label=label,
            started_at=started.isoformat(),
            results=results,
            executed=execute,
            repeats=repeats,
        )
        self._attach_cost(report, started)
        return report

    def _write_progress(self, index: int, total: int, result) -> None:
        if result.error:
            mark, style = "E", self.style.ERROR
        elif result.advisory:
            mark, style = "~", self.style.WARNING
        elif result.passed:
            mark, style = ".", self.style.SUCCESS
        else:
            mark, style = "F", self.style.ERROR
        self.stdout.write(
            style(f"  [{index:>3}/{total}] {mark} {result.case_id:>6}  {result.prompt[:56]}")
        )

    # ── Model override ─────────────────────────────────────

    def _apply_model(self, target: str):
        """Point SiteLLMConfig at `provider:model`; return a restore callable.

        ``force_local_ollama`` is cleared for the duration, exactly as
        ``smoke_llm_providers`` does — otherwise a cloud sweep would silently
        run every call against local Ollama and the comparison would be a lie.
        """
        from core.models import SiteLLMConfig

        provider, _, model = target.partition(":")
        provider = provider.strip().lower()
        valid = {c[0] for c in SiteLLMConfig.Provider.choices}
        if provider not in valid:
            raise CommandError(f"Unknown provider {provider!r}. Expected one of {sorted(valid)}.")

        config = SiteLLMConfig.load()
        original = {
            "modify_provider": config.modify_provider,
            "modify_model": config.modify_model,
            "force_local_ollama": config.force_local_ollama,
        }
        config.modify_provider = provider
        if model.strip():
            config.modify_model = model.strip()
        config.force_local_ollama = False
        config.save()

        def restore() -> None:
            current = SiteLLMConfig.load()
            for key, value in original.items():
                setattr(current, key, value)
            current.save()

        return restore

    def _current_model_label(self) -> str:
        from core.models import SiteLLMConfig

        config = SiteLLMConfig.load()
        if config.force_local_ollama:
            return "ollama (forced)"
        return f"{config.modify_provider}:{config.modify_model or 'default'}"

    def _attach_cost(self, report: BenchmarkReport, since) -> None:
        """Sum the LLMCallLog rows this pass created.

        Filtered by timestamp rather than a row-count offset so a concurrent
        request during a long run cannot shift the window.
        """
        from django.db.models import Sum

        from core.models import LLMCallLog

        totals = LLMCallLog.objects.filter(created_at__gte=since).aggregate(
            tokens_in=Sum("tokens_in"),
            tokens_out=Sum("tokens_out"),
            cost=Sum("estimated_cost_usd"),
        )
        report.tokens_in = totals["tokens_in"] or 0
        report.tokens_out = totals["tokens_out"] or 0
        report.estimated_cost_usd = float(totals["cost"] or 0)

    # ── Resolution helpers ─────────────────────────────────

    def _resolve_project(self, ident: str) -> Project:
        # A non-UUID string raises ValidationError on the UUID field, so catch
        # it too or the name lookup below is unreachable.
        try:
            return Project.objects.get(id=ident)
        except (Project.DoesNotExist, ValueError, ValidationError):
            pass
        try:
            return Project.objects.get(name__iexact=ident)
        except Project.DoesNotExist:
            raise CommandError(f"Project not found: {ident!r}")
        except Project.MultipleObjectsReturned:
            raise CommandError(f"Multiple projects match {ident!r} — pass a UUID instead.")

    def _resolve_ifc_file(self, project):
        from ifc_processor.models import IFCFile

        # Same selector the pipeline itself uses, so the benchmark scores the
        # file a real request would target.
        ifc_file = (
            IFCFile.objects.filter(project=project, status="completed")
            .order_by("-created_at")
            .first()
        )
        if ifc_file is None:
            raise CommandError(
                f"Project {project.name!r} has no processed IFC file. "
                "Upload one and let the pipeline finish before benchmarking."
            )
        if not Path(ifc_file.file.path).exists():
            raise CommandError(f"IFC file missing from storage: {ifc_file.file.path}")
        return ifc_file

    def _load_cases(self, corpus: str, section_filter: str):
        sections = {s.strip() for s in section_filter.split(",") if s.strip()} or None
        try:
            cases = parse_corpus(corpus, sections=sections)
        except CorpusError as e:
            raise CommandError(str(e))
        if not cases:
            raise CommandError(f"No cases matched filter {section_filter!r}.")
        return cases

    @staticmethod
    def _artifact_path(base: str, report: BenchmarkReport, count: int) -> Path:
        """One file per model when sweeping, so a sweep does not overwrite itself."""
        path = Path(base)
        if count == 1:
            return path
        slug = report.model_label.replace(":", "-").replace("/", "-").replace(" ", "-")
        return path.with_name(f"{path.stem}.{slug}{path.suffix or '.json'}")

    def _purge_failure_records(self, project) -> None:
        """Drop the FailureRecords this run produced.

        Roughly a third of the corpus expects a rejection, and every rejection
        writes a FailureRecord. Left alone, a sweep would bury real production
        failures under hundreds of expected ones.

        Scoped to this project and this run's time window. A genuine failure
        raised by someone using the same project mid-run would be caught in
        that window too — benchmark against a project nobody else is using, or
        pass ``--keep-failure-records``.
        """
        from metacastor.models import FailureRecord

        deleted, _ = FailureRecord.objects.filter(
            project=project, created_at__gte=self._run_started
        ).delete()
        if deleted:
            self.stdout.write(f"purged {deleted} FailureRecord(s) created by this run")
