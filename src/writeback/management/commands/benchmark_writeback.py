# writeback/management/commands/benchmark_writeback.py
"""Score the writeback pipeline on a corpus of natural-language prompts.

Runs real prompts through the real V3 pipeline against a real model on a
scratch copy of the project's IFC, and reports whether the right entities were
selected (targets match), whether the measured diff is the expected one (diff
match), whether nothing else moved (integrity), and whether requests that must
be declined were (reject / no-change).

Nothing here is a mock. That is the point — unit tests already cover the stages
with a stubbed LLM; this answers the question those cannot: does the system
handle real language, with this model, today.

Usage::

    # A section, saved for later comparison.
    cd src && uv run manage.py benchmark_writeback \\
        --project <uuid> --filter 1,3 --json ../runs/today.json

    # Bake-off rows (one artifact per model).
    cd src && uv run manage.py benchmark_writeback --project <uuid> \\
        --model ollama:qwen2.5-coder:7b --model anthropic:claude-sonnet-4-6 \\
        --repeat 2 --json ../runs/bakeoff.json

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
        "score targets match, diff match, integrity and rejections."
    )

    def add_arguments(self, parser):
        parser.add_argument("--project", required=True, help="Project UUID or name.")
        parser.add_argument(
            "--user",
            default="",
            help=(
                "Run as this user (email or username), so UserLLMConfig overrides apply the "
                "way a real Modify request would see them. Omitted: user=None, site defaults "
                "only, no per-user override — that path has already produced misleading "
                "verification twice."
            ),
        )
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
                "Model to benchmark, e.g. 'ollama:qwen2.5-coder:7b' or "
                "'anthropic:claude-sonnet-4-6'. Repeat to sweep. "
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
            help=(
                "Run each case up to N times; the first failing run is the one reported. "
                "A case that already failed is not repeated, nor is an advisory case, and "
                "a run the model could not answer never replaces a scored run."
            ),
        )
        parser.add_argument("--json", default="", help="Write the run artifact here.")
        parser.add_argument("--baseline", default="", help="Diff against a previous artifact.")
        parser.add_argument(
            "--note",
            action="append",
            default=[],
            metavar="KEY=VALUE",
            help="Free-form note stored in the artifact, e.g. --note vram=8GB --note offload=yes.",
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

    def handle(self, *args, **options):
        # Stamped before any case runs so the FailureRecord purge has a precise
        # lower bound — records that predate the run must survive it.
        self._run_started = datetime.now(UTC)

        project = self._resolve_project(options["project"])
        ifc_file = self._resolve_ifc_file(project)
        user = self._resolve_user(options["user"])
        cases = self._load_cases(options["corpus"], options["filter"])
        repeats = max(1, options["repeat"])
        notes = dict(n.partition("=")[::2] for n in options["note"] if "=" in n)

        self.stdout.write(
            f"{len(cases)} case(s) from {Path(options['corpus']).name} "
            f"against project {project.name!r} / {Path(ifc_file.file.name).name}"
            + (f", as user {user}" if user else "")
        )

        targets = options["model"] or [""]
        reports = [
            self._run_one_model(
                target, project, ifc_file, cases, user=user, repeats=repeats, notes=notes
            )
            for target in targets
        ]

        self.stdout.write(render_report(reports))

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
        self, target: str, project, ifc_file, cases, *, user, repeats: int, notes: dict
    ) -> BenchmarkReport:
        """Run the corpus once under `target`, restoring site config after."""
        label = target or self._current_model_label()
        started = datetime.now(UTC)

        restore = self._apply_model(target) if target else None
        try:
            runner = BenchmarkRunner(project, ifc_file=ifc_file, user=user)
            results = []
            self.stdout.write(self.style.MIGRATE_HEADING(f"\n=== {label} ==="))
            for index, case in enumerate(cases, start=1):
                result = self._run_case_repeated(runner, case, repeats)
                results.append(result)
                self._write_progress(index, len(cases), result)
        finally:
            if restore:
                restore()

        report = BenchmarkReport(
            model_label=label,
            started_at=started.isoformat(),
            results=results,
            repeats=repeats,
            notes=notes,
        )
        self._attach_cost(report, started)
        return report

    @staticmethod
    def _run_case_repeated(runner: BenchmarkRunner, case, repeats: int):
        """Run a case up to ``repeats`` times and keep the worst scored run.

        Re-running is how LLM non-determinism is surfaced rather than averaged
        away. A case that has already failed is not run again; an advisory
        case is never repeated; a run the model could not answer (a harness
        error) is neither a failure nor a scored run: it does not replace a
        scored run, and it is retried while the budget lasts. ``runs`` records
        how many times the case actually ran.
        """
        result = runner.run_case(case)
        runs = 1
        while runs < repeats and (result.passed or result.error) and not case.advisory:
            retry = runner.run_case(case)
            runs += 1
            if retry.error:
                continue
            if result.error or not retry.passed:
                result = retry
            if not result.passed:
                break
        result.runs = runs
        return result

    def _write_progress(self, index: int, total: int, result) -> None:
        if result.error:
            mark, style = "E", self.style.ERROR
        elif result.advisory:
            mark, style = "~", self.style.WARNING
        elif result.passed:
            mark, style = ".", self.style.SUCCESS
        else:
            mark, style = "F", self.style.ERROR
        repairs = (
            f" (+{result.repairs} repair{'s' if result.repairs != 1 else ''})"
            if result.repairs
            else ""
        )
        self.stdout.write(
            style(
                f"  [{index:>3}/{total}] {mark} {result.case_id:>6}  {result.prompt[:56]}"
                f"  {result.duration_seconds:.0f}s{repairs}"
            )
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

    def _resolve_user(self, ident: str):
        """The account to run as, so UserLLMConfig overrides apply like a real request.

        Empty: None, which resolves to SiteLLMConfig defaults only, no BYOK and no per-user
        Ollama override — the anonymous path that has already produced misleading
        verification twice.
        """
        if not ident:
            return None
        from django.contrib.auth import get_user_model

        user_model = get_user_model()
        try:
            return user_model.objects.get(email__iexact=ident)
        except user_model.DoesNotExist:
            pass
        try:
            return user_model.objects.get(username__iexact=ident)
        except user_model.DoesNotExist:
            raise CommandError(f"User not found: {ident!r}")

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
