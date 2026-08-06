# writeback/management/commands/dry_run_v2_pipeline.py
"""Run the writeback pipeline on a user prompt and print each stage.

Read-only: no proposal is created, no IFC file is modified, no git
commit is made. Stops after the tier router decides which dispatch
path the request would take.

Drives ``ProposalPipeline.route_request`` — the same stage walk the real
propose path uses — so this command can never drift from production
behaviour.

Use this for ad-hoc debugging of a single problematic request: it prints
every stage so you can see where a prompt goes wrong.

For batch runs over the corpus, use ``benchmark_writeback`` instead — it
parses the expectations declared in ``pipeline-test-prompts.txt`` and
scores them, rather than leaving a human to diff 92 stage dumps by eye.

Usage::

    uv run manage.py dry_run_v2_pipeline \\
        --project <uuid-or-name> \\
        --prompt "Create three new IfcZone for Fire Zone A, B, and C"

    # Multi-prompt batch from a file (one prompt per non-empty line).
    uv run manage.py dry_run_v2_pipeline \\
        --project <uuid-or-name> \\
        --prompts-file prompts.txt
"""

from __future__ import annotations

import json
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from environments.models import Project
from writeback.services.emitters import StdoutEmitter
from writeback.services.errors import ModificationError
from writeback.services.proposal_pipeline import ProposalPipeline


class Command(BaseCommand):
    help = (
        "Dry-run the writeback pipeline on a user prompt. "
        "Prints triage segments, slots, resolutions, and routing — "
        "then stops without creating a proposal."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--project",
            type=str,
            required=True,
            help="Project UUID or (case-insensitive) project name.",
        )
        parser.add_argument(
            "--prompt",
            type=str,
            default=None,
            help="A single user prompt to dry-run.",
        )
        parser.add_argument(
            "--prompts-file",
            type=str,
            default=None,
            help="Path to a text file with one prompt per non-empty line.",
        )

    def handle(self, *args, **options):
        prompt = options.get("prompt")
        prompts_file = options.get("prompts_file")
        if not prompt and not prompts_file:
            raise CommandError("Provide either --prompt or --prompts-file.")
        if prompt and prompts_file:
            raise CommandError("Use --prompt OR --prompts-file, not both.")

        project = self._resolve_project(options["project"])
        pipeline = ProposalPipeline(project, user=None)
        emitter = StdoutEmitter(self.stdout)

        prompts = [prompt] if prompt else self._read_prompts_file(prompts_file)

        for index, current in enumerate(prompts, start=1):
            self.stdout.write("")
            header = f"=== prompt {index}/{len(prompts)} ==="
            self.stdout.write(self.style.MIGRATE_HEADING(header))
            self.stdout.write(f"  {current!r}")
            self._dry_run_one(current, pipeline, emitter)

    # ── Internals ──────────────────────────────────────────────

    def _resolve_project(self, ident: str) -> Project:
        # A non-UUID string raises ValidationError (not ValueError) on the
        # UUID field, so catch it too or the documented name lookup below
        # is unreachable.
        try:
            return Project.objects.get(id=ident)
        except (Project.DoesNotExist, ValueError, ValidationError):
            pass
        try:
            return Project.objects.get(name__iexact=ident)
        except Project.DoesNotExist:
            raise CommandError(f"Project not found: {ident!r}")
        except Project.MultipleObjectsReturned:
            raise CommandError(f"Multiple projects match name {ident!r} — pass UUID instead.")

    def _read_prompts_file(self, path: str) -> list[str]:
        p = Path(path)
        if not p.exists():
            raise CommandError(f"Prompts file not found: {path}")
        lines = [ln.strip() for ln in p.read_text(encoding="utf-8").splitlines()]
        prompts = [ln for ln in lines if ln and not ln.startswith("#")]
        if not prompts:
            raise CommandError(f"No prompts found in {path}.")
        return prompts

    def _dry_run_one(
        self,
        prompt: str,
        pipeline: ProposalPipeline,
        emitter: StdoutEmitter,
    ) -> None:
        """Walk the real pipeline stages, printing progress, then stop."""
        try:
            segments, routing = pipeline.route_request(prompt, emitter=emitter)
        except ModificationError as e:
            # Rejections and stage failures both surface here; the emitter has
            # already printed the failing stage and its message.
            self.stdout.write(self.style.WARNING(f"  -> rejected: {e}"))
            return

        for i, seg in enumerate(segments, start=1):
            resolution = seg.get("resolution")
            diagnostic = getattr(resolution, "diagnostic", "n/a")
            self.stdout.write(
                f"  segment {i} ({seg.get('kind')}): "
                f"slots={self._compact_json(seg.get('slots'))} "
                f"resolution={diagnostic}"
            )
            for w in seg.get("warnings") or []:
                self.stdout.write(self.style.WARNING(f"    ⚠ {w}"))

        self.stdout.write(
            self.style.SUCCESS(f"  -> tier {routing.tier}, operation {routing.operation}")
        )
        self.stdout.write("  (dry-run stops here -- no proposal created)")

    @staticmethod
    def _compact_json(obj) -> str:
        """Render a small dict on one line. Falls back to repr for non-JSON."""
        try:
            return json.dumps(obj, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            return repr(obj)
