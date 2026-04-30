# writeback/management/commands/dry_run_v2_pipeline.py
"""Run the V2 writeback pipeline on a user prompt and print each stage.

Read-only: no proposal is created, no IFC file is modified, no git
commit is made. Stops after the tier router decides which dispatch
path the request would take.

Use this to validate the V2 prompts against your real Ollama models
before flipping ``WRITEBACK_PIPELINE_V2 = True`` globally.

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

from django.core.management.base import BaseCommand, CommandError

from environments.models import Project
from writeback.services.entity_resolver import (
    MODE_EXISTING_TARGET,
    MODE_NEW_TARGET,
    MODE_PARENT_TARGET,
    EntityNameResolver,
)
from writeback.services.slot_extractor import SlotExtractionError, SlotExtractor
from writeback.services.tier_router import route as route_tier
from writeback.services.triage_classifier import TriageClassifier, TriageError


class Command(BaseCommand):
    help = (
        "Dry-run the V2 writeback pipeline on a user prompt. "
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

        triage = TriageClassifier(user=None)
        slots = SlotExtractor(user=None)
        resolver = EntityNameResolver(project=project, user=None)

        prompts = (
            [prompt]
            if prompt
            else self._read_prompts_file(prompts_file)
        )

        for index, current in enumerate(prompts, start=1):
            self.stdout.write("")
            header = f"=== prompt {index}/{len(prompts)} ==="
            self.stdout.write(self.style.MIGRATE_HEADING(header))
            self.stdout.write(f"  {current!r}")
            self._dry_run_one(current, triage, slots, resolver)

    # ── Internals ──────────────────────────────────────────────

    def _resolve_project(self, ident: str) -> Project:
        try:
            return Project.objects.get(id=ident)
        except (Project.DoesNotExist, ValueError):
            pass
        try:
            return Project.objects.get(name__iexact=ident)
        except Project.DoesNotExist:
            raise CommandError(f"Project not found: {ident!r}")
        except Project.MultipleObjectsReturned:
            raise CommandError(
                f"Multiple projects match name {ident!r} — pass UUID instead."
            )

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
        triage: TriageClassifier,
        slots: SlotExtractor,
        resolver: EntityNameResolver,
    ) -> None:
        # Stage 1 — Triage.
        self.stdout.write("\n[1] triage_classifier.classify")
        try:
            triage_result = triage.classify(prompt)
        except TriageError as e:
            self.stderr.write(self.style.ERROR(f"  TriageError: {e}"))
            return
        segments = triage_result.segments
        for i, seg in enumerate(segments, start=1):
            self.stdout.write(f"  segment {i}: {self._compact_json(seg)}")

        # Stage 2 — Slot extraction per segment.
        self.stdout.write("\n[2] slot_extractor.extract")
        for i, seg in enumerate(segments, start=1):
            kind = seg.get("kind")
            if kind in ("OUT_OF_SCOPE", "UNCLEAR"):
                self.stdout.write(f"  segment {i} ({kind}): no slots needed")
                seg["slots"] = {}
                seg.setdefault("warnings", [])
                continue
            try:
                slot_result = slots.extract(seg, prompt)
            except SlotExtractionError as e:
                self.stderr.write(
                    self.style.ERROR(f"  segment {i} SlotExtractionError: {e}")
                )
                return
            seg["slots"] = slot_result.slots
            seg["warnings"] = slot_result.warnings
            self.stdout.write(f"  segment {i} slots: {self._compact_json(slot_result.slots)}")
            if slot_result.warnings:
                for w in slot_result.warnings:
                    self.stdout.write(self.style.WARNING(f"    ⚠ {w}"))

        # Stage 3 — Resolver per segment, mode-aware.
        self.stdout.write("\n[3] entity_resolver.resolve")
        for i, seg in enumerate(segments, start=1):
            kind = seg.get("kind")
            target = (seg.get("target_phrase") or "").strip()
            if kind in ("PROPERTY", "ATTRIBUTE", "PSET", "DELETE", "RELATIONSHIP"):
                resolution = resolver.resolve(target, mode=MODE_EXISTING_TARGET)
                seg["resolution"] = resolution
                self.stdout.write(
                    f"  segment {i} ({kind}, EXISTING_TARGET on {target!r}): "
                    f"{resolution.diagnostic}"
                )
            elif kind == "CREATE":
                parent_phrase = (seg.get("slots") or {}).get("parent_phrase") or ""
                if parent_phrase:
                    parent = resolver.resolve(parent_phrase, mode=MODE_PARENT_TARGET)
                    seg["parent_resolution"] = parent
                    self.stdout.write(
                        f"  segment {i} (CREATE, PARENT_TARGET on "
                        f"{parent_phrase!r}): {parent.diagnostic}"
                    )
                else:
                    seg["parent_resolution"] = None
                    self.stdout.write(
                        f"  segment {i} (CREATE, no parent_phrase)"
                    )
                seg["resolution"] = resolver.resolve("", mode=MODE_NEW_TARGET)
            else:
                seg["resolution"] = None
                self.stdout.write(f"  segment {i} ({kind}): resolver skipped")

        # Stage 3.5 — Tier router (deterministic).
        self.stdout.write("\n[4] tier_router.route")
        routing = route_tier(segments)
        if routing.is_rejected:
            self.stdout.write(
                self.style.WARNING(
                    f"  → tier 0 REJECT: {routing.rejection_reason}"
                )
            )
            return
        self.stdout.write(
            self.style.SUCCESS(
                f"  → tier {routing.tier}, operation {routing.operation}"
            )
        )
        self.stdout.write(
            "  (dry-run stops here — no proposal created)"
        )

    @staticmethod
    def _compact_json(obj) -> str:
        """Render a small dict on one line. Falls back to repr for non-JSON."""
        try:
            return json.dumps(obj, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            return repr(obj)
