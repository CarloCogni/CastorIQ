# embeddings/management/commands/generate_embeddings.py

import time
import logging
from typing import List
from django.core.management.base import BaseCommand
from django.db.models import QuerySet

from ifc_processor.models import IFCEntity, IFCFile
from embeddings.services.embedding_service import EmbeddingService

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Generate vector embeddings for IFC entities using Ollama."

    def add_arguments(self, parser):
        parser.add_argument(
            "--project",
            type=str,
            help="UUID of the project to process.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Regenerate embeddings even if they already exist.",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=50,
            help="Number of entities to embed in a single Ollama request.",
        )

    def handle(self, *args, **options):
        """
        Main entry point for the command.
        """
        start_time = time.time()
        batch_size = options["batch_size"]

        # 1. Identify what needs processing
        queryset = self._get_target_entities(options)
        total_count = queryset.count()

        if total_count == 0:
            self.stdout.write(self.style.WARNING("No entities found needing embeddings."))
            return

        self.stdout.write(f"Found {total_count} entities to process.")

        # 2. Initialize Service
        try:
            service = EmbeddingService()
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Failed to initialize EmbeddingService: {e}"))
            return

        # 3. Process in batches
        # We fetch all IDs first to avoid pagination shifts when filtering by isnull=True
        all_ids = list(queryset.values_list('id', flat=True))

        processed_count = 0
        skipped_count = 0

        for i in range(0, len(all_ids), batch_size):
            chunk_ids = all_ids[i: i + batch_size]

            # Process the chunk
            n_embedded, n_skipped = self._process_batch(chunk_ids, service)

            processed_count += n_embedded
            skipped_count += n_skipped

            # Progress update
            progress = min(i + batch_size, total_count)
            self._print_progress(progress, total_count, processed_count)

        # 4. Final Summary
        duration = time.time() - start_time
        self.stdout.write(self.style.SUCCESS(
            f"\n\nComplete! Processed {processed_count} entities "
            f"({skipped_count} skipped empty descriptions) in {duration:.1f}s."
        ))

    def _get_target_entities(self, options) -> QuerySet:
        """
        Build the initial queryset based on command line arguments.
        """
        qs = IFCEntity.objects.filter(ifc_file__status=IFCFile.Status.COMPLETED)

        if options["project"]:
            qs = qs.filter(ifc_file__project__id=options["project"])

        if not options["force"]:
            qs = qs.filter(embedding__isnull=True)

        return qs

    def _process_batch(self, entity_ids: List[str], service: EmbeddingService) -> tuple[int, int]:
        """
        Fetch entities, generate embeddings, and bulk update.
        Returns (number_embedded, number_skipped).
        """
        # Fetch actual objects for this batch
        entities = list(IFCEntity.objects.filter(id__in=entity_ids))

        # Filter out entities with empty descriptions (waste of compute)
        valid_entities = [e for e in entities if e.description and e.description.strip()]
        skipped_entities = len(entities) - len(valid_entities)

        if not valid_entities:
            return 0, skipped_entities

        try:
            # Extract text
            texts = [e.description for e in valid_entities]

            # Call AI Service
            vectors = service.embed_documents(texts)

            # Assign vectors to objects
            for entity, vector in zip(valid_entities, vectors):
                entity.embedding = vector

            # Bulk save to DB (efficient single query)
            IFCEntity.objects.bulk_update(valid_entities, ["embedding"])

            return len(valid_entities), skipped_entities

        except Exception as e:
            logger.error(f"Batch processing failed: {e}")
            self.stdout.write(self.style.ERROR(f"\nBatch failed: {e}"))
            return 0, len(entities)

    def _print_progress(self, current, total, embedded):
        percent = (current / total) * 100
        self.stdout.write(
            f"\rProcessing: [{current}/{total}] {percent:.1f}% | Embedded: {embedded}",
            ending=""
        )