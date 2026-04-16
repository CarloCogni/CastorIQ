# metacastor/management/commands/export_training_data.py
"""
Management command: export_training_data

Exports approved SkillExamples to Axolotl-compatible JSONL for QLoRA fine-tuning.

Selection rule (matches MetaCastor D4 spec):
    was_approved=True AND commit_success=True

Each line of output is a single JSON object:
    {
        "instruction": <verbatim IntentClassifier SYSTEM_PROMPT>,
        "input":       "User query: <original user request>",
        "output":      <SkillExample.intent_json, compact JSON>
    }

The instruction field is imported directly from writeback.services.intent_classifier
so the training format matches the production inference format byte-for-byte. Any
divergence (a "training-only" simplified prompt) would silently bias the fine-tuned
model away from the prompt it sees at inference time.

Known limitation (documented in docs/metacastor/d4-finetuning-pipeline.md):
SkillExample does not persist `entity_context`. The input field therefore contains
only the user query. Entity context is rebuilt from live IFC state at inference
time — there is no faithful way to reconstruct the entity_context that was visible
at harvest time.

Usage:
    uv run manage.py export_training_data
    uv run manage.py export_training_data --organic-only --min-examples 100
    uv run manage.py export_training_data --project <uuid> --output /tmp/foo.jsonl
"""

import json
import logging
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from metacastor.models import SkillExample
from writeback.services.intent_classifier import SYSTEM_PROMPT

logger = logging.getLogger(__name__)

# Default output lives next to run_eval.py and the Axolotl YAML:
# src/metacastor/eval/training_export.jsonl
_DEFAULT_OUTPUT = Path(__file__).parents[2] / "eval" / "training_export.jsonl"


class Command(BaseCommand):
    """Export SkillExamples to Axolotl-compatible JSONL for QLoRA fine-tuning."""

    help = "Export approved SkillExamples to Axolotl JSONL for QLoRA fine-tuning."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--output",
            type=Path,
            default=_DEFAULT_OUTPUT,
            help=f"Output JSONL path. Default: {_DEFAULT_OUTPUT}",
        )
        parser.add_argument(
            "--project",
            type=str,
            default=None,
            help="Limit export to a single project UUID. Default: all projects.",
        )
        parser.add_argument(
            "--min-examples",
            type=int,
            default=50,
            help="Warn (and skip writing) if fewer than N examples match. Default: 50.",
        )
        parser.add_argument(
            "--organic-only",
            action="store_true",
            help="Exclude synthetic (is_organic=False) examples from the export.",
        )

    def handle(self, *args, **options) -> None:
        output_path: Path = options["output"]
        project_id: str | None = options["project"]
        min_examples: int = options["min_examples"]
        organic_only: bool = options["organic_only"]

        queryset = SkillExample.objects.filter(
            was_approved=True,
            commit_success=True,
        )
        if project_id:
            queryset = queryset.filter(project_id=project_id)
        if organic_only:
            queryset = queryset.filter(is_organic=True)

        queryset = queryset.only(
            "query_text", "intent_json", "outcome_tier", "is_organic"
        ).order_by("created_at")

        total = queryset.count()
        if total < min_examples:
            self.stdout.write(
                self.style.WARNING(
                    f"Only {total} example(s) match (threshold: {min_examples}). "
                    "No file written. Lower --min-examples or wait for more data."
                )
            )
            return

        output_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")

        written = 0
        skipped = 0
        try:
            with tmp_path.open("w", encoding="utf-8") as fh:
                for sk in queryset.iterator():
                    line = self._serialize_example(sk)
                    if line is None:
                        skipped += 1
                        continue
                    fh.write(line)
                    fh.write("\n")
                    written += 1
            tmp_path.replace(output_path)
        except Exception as exc:
            if tmp_path.exists():
                tmp_path.unlink()
            raise CommandError(f"Export failed: {exc}") from exc

        self.stdout.write(
            self.style.SUCCESS(
                f"Exported {written} example(s) to {output_path} "
                f"({skipped} skipped due to empty intent_json)."
            )
        )

    @staticmethod
    def _serialize_example(sk: SkillExample) -> str | None:
        """Serialize one SkillExample to an Axolotl JSONL line. Returns None to skip."""
        if not sk.intent_json:
            # Defensive: a SkillExample with empty intent_json has nothing to teach.
            return None
        record = {
            "instruction": SYSTEM_PROMPT,
            "input": f"User query: {sk.query_text}",
            "output": json.dumps(sk.intent_json, separators=(",", ":")),
        }
        return json.dumps(record, ensure_ascii=False)
