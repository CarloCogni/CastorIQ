# metacastor/management/commands/seed_tier3_bank.py
"""
Management command: seed_tier3_bank

Loads t3_reference_bank.jsonl and creates synthetic SkillExample records
carrying `generated_code`. These act as certified Tier 3 reference patterns
that the skill retriever can surface to the Tier 3 planner (Deliverable 5).

Synthetic examples:
  - is_organic = False
  - project = None  (global — available to all projects)
  - was_approved = True, commit_success = True  (manually curated ground truth)
  - outcome_tier = 3
  - generated_code = <working modify_ifc(model) body>

Safe to re-run: uses get_or_create keyed on (query_text, is_organic=False).

Usage:
    uv run manage.py seed_tier3_bank
    uv run manage.py seed_tier3_bank --clear           # wipe tier-3 synthetic examples first
    uv run manage.py seed_tier3_bank --skip-embeddings # skip embedding generation
"""

import json
import logging
from pathlib import Path

from django.core.management.base import BaseCommand

from embeddings.services.embedding_service import EmbeddingService
from metacastor.models import SkillExample
from metacastor.services.entity_type_extractor import extract_entity_types

logger = logging.getLogger(__name__)

# src/metacastor/management/commands/seed_tier3_bank.py
# parents[0] = commands/, [1] = management/, [2] = metacastor/
_BANK_PATH = Path(__file__).parents[2] / "eval" / "t3_reference_bank.jsonl"


class Command(BaseCommand):
    """Seed the skill bank with certified Tier 3 reference patterns."""

    help = "Seed the MetaCastor skill bank from t3_reference_bank.jsonl."

    def add_arguments(self, parser):
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Delete existing synthetic (is_organic=False) tier-3 examples before seeding.",
        )
        parser.add_argument(
            "--skip-embeddings",
            action="store_true",
            help="Skip embedding generation (records stored with null query_embedding).",
        )
        parser.add_argument(
            "--file",
            type=str,
            default=None,
            help="Path to an alternate JSONL file. Defaults to t3_reference_bank.jsonl.",
        )

    def handle(self, *args, **options):
        path = Path(options["file"]) if options["file"] else _BANK_PATH
        if not path.exists():
            self.stderr.write(self.style.ERROR(f"File not found: {path}"))
            return

        if options["clear"]:
            deleted, _ = SkillExample.objects.filter(is_organic=False, outcome_tier=3).delete()
            self.stdout.write(self.style.WARNING(f"Cleared {deleted} synthetic tier-3 examples."))

        embedder = EmbeddingService() if not options["skip_embeddings"] else None

        created_count = 0
        skipped_count = 0
        error_count = 0

        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue

            try:
                record = json.loads(line)
            except json.JSONDecodeError as e:
                self.stderr.write(self.style.ERROR(f"JSON parse error: {e}"))
                error_count += 1
                continue

            query = record.get("query_text", "").strip()
            code = record.get("generated_code", "").strip()
            if not query or not code:
                self.stderr.write(
                    self.style.WARNING(
                        f"Skipping record with missing query_text or generated_code: {record.get('id', '?')}"
                    )
                )
                error_count += 1
                continue

            entity_types = record.get("entity_types") or extract_entity_types(query)

            intent_json = {
                "tier": 3,
                "operation": "CODE",
                "filter": {},
                "confidence": 90,
                "code": code,
                "explanation": record.get("explanation", "Certified Tier 3 reference pattern."),
            }

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
                    "intent_json": intent_json,
                    "entity_types": entity_types,
                    "outcome_tier": 3,
                    "was_approved": True,
                    "commit_success": True,
                    "generated_code": code,
                },
            )

            if created:
                created_count += 1
            else:
                skipped_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Tier-3 seeding complete: {created_count} created, "
                f"{skipped_count} already existed, {error_count} errors."
            )
        )
