import logging
from django.db import transaction
from django.utils import timezone

from ifc_processor.models import IFCFile, IFCEntity
from ifc_processor.services.parser import IFCParser
from embeddings.services.embedding_service import EmbeddingService

logger = logging.getLogger(__name__)


class IFCProcessingService:
    """
    Orchestrates the full processing pipeline for an IFC file:
    1. Calculate Hash
    2. Parse Geometry & Data (IfcOpenShell)
    3. Generate Semantic Embeddings (Ollama)
    """

    def __init__(self, ifc_file: IFCFile):
        self.ifc_file = ifc_file

    def run_pipeline(self) -> bool:
        """
        Run the complete processing workflow.
        Returns True if successful, False otherwise.
        """
        try:
            # 1. Calculate and update hash
            # We do this first to ensure data integrity
            self.ifc_file.file_hash = self.ifc_file.calculate_hash()
            self.ifc_file.save(update_fields=["file_hash"])

            # 2. Parse IFC (Extract Entities)
            parser = IFCParser(self.ifc_file)
            parse_success = parser.parse()

            if not parse_success:
                logger.error(f"Parsing failed for {self.ifc_file.name}")
                return False

            # 3. Generate Embeddings
            # We only embed entities that have a description
            embed_ok = self._generate_embeddings()
            if not embed_ok:
                logger.warning(f"Embeddings failed for {self.ifc_file.name} — entities parsed but not searchable")
                # File is still "completed" for parsing, but worth noting

            return True

        except Exception as e:
            logger.exception(f"Pipeline failed for {self.ifc_file.name}: {e}")
            self.ifc_file.status = IFCFile.Status.FAILED
            self.ifc_file.error_message = str(e)
            self.ifc_file.save(update_fields=['status', 'error_message'])
            return False

    def _generate_embeddings(self) -> bool:
        """Returns True if embedding succeeded, False otherwise."""
        entities = self.ifc_file.entities.filter(description__isnull=False).exclude(description="")
        if not entities.exists():
            return True  # Nothing to embed is not an error

        try:
            service = EmbeddingService()
            entity_list = list(entities)
            texts = [e.description for e in entity_list]
            vectors = service.embed_documents(texts)

            for entity, vector in zip(entity_list, vectors):
                entity.embedding = vector

            IFCEntity.objects.bulk_update(entity_list, ['embedding'])
            logger.info(f"Embedded {len(entity_list)} entities for {self.ifc_file.name}")
            return True

        except Exception as e:
            logger.error(f"Embedding generation failed: {e}")
            return False

