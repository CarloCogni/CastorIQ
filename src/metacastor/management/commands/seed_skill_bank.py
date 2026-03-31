# metacastor/management/commands/seed_skill_bank.py
"""
Management command: seed_skill_bank

Loads dev_cases.jsonl and creates synthetic SkillExample records for cold-start.

Synthetic examples:
  - is_organic = False
  - project = None  (global — available to all projects)
  - was_approved = True, commit_success = True (manually curated ground truth)

Safe to re-run: uses get_or_create keyed on (query_text, is_organic=False).

Usage:
    uv run manage.py seed_skill_bank
    uv run manage.py seed_skill_bank --clear   # wipe synthetic examples first
"""

import json
import logging
from pathlib import Path

from django.core.management.base import BaseCommand

from embeddings.services.embedding_service import EmbeddingService
from metacastor.models import SkillExample
from metacastor.services.entity_type_extractor import extract_entity_types

logger = logging.getLogger(__name__)

# dev_cases.jsonl lives at src/metacastor/eval/dev_cases.jsonl
# This file is at  src/metacastor/management/commands/seed_skill_bank.py
# parents[0] = commands/, [1] = management/, [2] = metacastor/
_DEV_CASES_PATH = Path(__file__).parents[2] / "eval" / "dev_cases.jsonl"


class Command(BaseCommand):
    """Seed the skill bank from dev_cases.jsonl (cold-start mitigation)."""

    help = "Seed the MetaCastor skill bank from dev_cases.jsonl."

    def add_arguments(self, parser):
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Delete all existing synthetic (is_organic=False) examples before seeding.",
        )
        parser.add_argument(
            "--skip-embeddings",
            action="store_true",
            help="Skip embedding generation (records stored with null query_embedding).",
        )

    def handle(self, *args, **options):
        if not _DEV_CASES_PATH.exists():
            self.stderr.write(self.style.ERROR(f"File not found: {_DEV_CASES_PATH}"))
            return

        if options["clear"]:
            deleted, _ = SkillExample.objects.filter(is_organic=False).delete()
            self.stdout.write(self.style.WARNING(f"Cleared {deleted} synthetic examples."))

        embedder = EmbeddingService() if not options["skip_embeddings"] else None

        created_count = 0
        skipped_count = 0
        error_count = 0

        lines = _DEV_CASES_PATH.read_text(encoding="utf-8").splitlines()
        for line in lines:
            line = line.strip()
            if not line:
                continue

            try:
                case = json.loads(line)
            except json.JSONDecodeError as e:
                self.stderr.write(self.style.ERROR(f"JSON parse error: {e}"))
                error_count += 1
                continue

            query = case.get("query", "")
            if not query:
                continue

            intent = case.get("ground_truth_intent", {})
            # Ground truth can be a dict (single) or list (chained)
            if isinstance(intent, list):
                outcome_tier = intent[0].get("tier", 1) if intent else 1
            else:
                outcome_tier = intent.get("tier", 1)

            entity_types = extract_entity_types(query)

            # Generate embedding — skip gracefully if Ollama is unavailable.
            embedding = None
            if embedder is not None:
                try:
                    embedding = embedder.embed_query(query)
                except Exception as e:
                    self.stderr.write(
                        self.style.WARNING(
                            f"Embedding failed for '{query[:50]}': {e} — stored without embedding."
                        )
                    )

            _, created = SkillExample.objects.get_or_create(
                query_text=query,
                is_organic=False,
                defaults={
                    "project": None,
                    "query_embedding": embedding,
                    "intent_json": intent,
                    "entity_types": entity_types,
                    "outcome_tier": outcome_tier,
                    "was_approved": True,
                    "commit_success": True,
                },
            )

            if created:
                created_count += 1
            else:
                skipped_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeding complete: {created_count} created, "
                f"{skipped_count} already existed, {error_count} errors."
            )
        )
