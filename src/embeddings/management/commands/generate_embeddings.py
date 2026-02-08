"""
Management command to generate embeddings for IFC entities.

Queries all entities with empty embedding fields, embeds their
semantic descriptions in batches, and bulk-updates the database.

Usage:
    uv run manage.py generate_embeddings
    uv run manage.py generate_embeddings --project <uuid>
    uv run manage.py generate_embeddings --force --batch-size 100
"""

import time
import logging

from django.core.management.base import BaseCommand

from ifc_processor.models import IFCEntity, IFCFile
from embeddings.services.embedding_service import EmbeddingService

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Generate vector embeddings for IFC entity descriptions."

    def add_arguments(self, parser):
        parser.add_argument(
            "--project",
            type=str,
            help="Only process entities belonging to this project UUID.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Re-embed all entities, even those that already have embeddings.",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=50,
            help="Number of entities to embed per batch (default: 50).",
        )

    def handle(self, *args, **options):
        service = EmbeddingService()
        queryset = self._build_queryset(options)

        total = queryset.count()
        if total == 0:
            self.stdout.write(self.style.WARNING("No entities to embed."))
            return

        self.stdout.write(
            f"Embedding {total} entities with '{service.model_name}' "
            f"(batch size: {options['batch_size']})..."
        )

        embedded = 0
        skipped = 0
        batch_size = options["batch_size"]
        start_time = time.time()

        # Process in batches to manage memory and Ollama load
        for batch_start in range(0, total, batch_size):
            batch = list(
                queryset
                .order_by("pk")[batch_start:batch_start + batch_size]
                .only("id", "description", "embedding")
            )

            # Separate entities with/without descriptions
            to_embed = [e for e in batch if e.description.strip()]
            no_description = [e for e in batch if not e.description.strip()]
            skipped += len(no_description)

            if not to_embed:
                continue

            # Call Ollama in one batch
            texts = [e.description for e in to_embed]
            try:
                vectors = service.embed_documents(texts)
            except Exception as e:
                self.stdout.write(self.style.ERROR(
                    f"  Batch failed at offset {batch_start}: {e}"
                ))
                logger.exception("Embedding batch failed")
                continue

            # Assign vectors and bulk update
            for entity, vector in zip(to_embed, vectors):
                entity.embedding = vector

            IFCEntity.objects.bulk_update(to_embed, ["embedding"])
            embedded += len(to_embed)

            self._print_progress(embedded, skipped, total)

        elapsed = time.time() - start_time
        self.stdout.write(self.style.SUCCESS(
            f"\n✓ Done: {embedded} embedded, {skipped} skipped "
            f"(no description) in {elapsed:.1f}s"
        ))

    def _build_queryset(self, options):
        """Build the entity queryset based on CLI flags."""
        qs = IFCEntity.objects.filter(
            ifc_file__status=IFCFile.Status.COMPLETED
        )

        if options.get("project"):
            qs = qs.filter(ifc_file__project_id=options["project"])

        if not options["force"]:
            qs = qs.filter(embedding__isnull=True)

        return qs

    def _print_progress(self, embedded, skipped, total):
        """Print a progress update to stdout."""
        processed = embedded + skipped
        percent = (processed / total * 100) if total else 0
        self.stdout.write(
            f"  [{processed}/{total}] {percent:.0f}% — "
            f"{embedded} embedded, {skipped} skipped",
            ending="\r",
        )
        self.stdout.flush()