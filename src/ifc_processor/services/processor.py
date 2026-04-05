# ifc_processor/services/processor.py
import logging

from embeddings.services.embedding_service import EmbeddingService
from ifc_processor.models import IFCEntity, IFCFile
from ifc_processor.services.parser import IFCParser

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
        self.git_commit_hash: str | None = None

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
                logger.warning(
                    f"Embeddings failed for {self.ifc_file.name} — entities parsed but not searchable"
                )
                # File is still "completed" for parsing, but worth noting

            # 4. Register in Git (initial commit — enables rollback to original)
            self._register_in_git()

            return True

        except Exception as e:
            logger.exception(f"Pipeline failed for {self.ifc_file.name}: {e}")
            self.ifc_file.status = IFCFile.Status.FAILED
            self.ifc_file.error_message = str(e)
            self.ifc_file.save(update_fields=["status", "error_message"])
            return False

    def _register_in_git(self) -> None:
        """Create the initial Git commit for this IFC file.

        Non-fatal: a Git failure must not prevent the file from being parsed
        and searchable. Uses a deferred import to avoid a circular dependency
        (writeback → ifc_processor already exists at module level).
        """
        try:
            from writeback.models import GitCommit
            from writeback.services.git_service import GitService

            git = GitService(self.ifc_file.project)
            git.ensure_repo()
            parent_hash = git.get_parent_hash()
            commit_hash = git.track_ifc(self.ifc_file)
            if commit_hash:
                self.git_commit_hash = commit_hash
                GitCommit.objects.create(
                    ifc_file=self.ifc_file,
                    commit_hash=commit_hash,
                    parent_hash=parent_hash,
                    message=f"[TRACK] Add {self.ifc_file.name}",
                    author=None,
                    diff_data={"operation": "TRACK"},
                )
                logger.info(f"Initial Git commit for {self.ifc_file.name}: {commit_hash[:8]}")
        except Exception as e:
            logger.error(f"Git tracking failed for {self.ifc_file.name}: {e}")

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

            IFCEntity.objects.bulk_update(entity_list, ["embedding"])
            logger.info(f"Embedded {len(entity_list)} entities for {self.ifc_file.name}")
            return True

        except Exception as e:
            logger.error(f"Embedding generation failed: {e}")
            return False
