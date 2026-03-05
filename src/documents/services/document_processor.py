# documents/services/document_processor.py
import logging
import os

from django.db import transaction
from django.utils import timezone

# We use LangChain loaders because they are far more robust than raw pypdf
from langchain_community.document_loaders import Docx2txtLoader, PyPDFLoader, TextLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter

from documents.models import Document, DocumentChunk
from embeddings.services.embedding_service import EmbeddingService

logger = logging.getLogger(__name__)


class DocumentProcessor:
    """
    Service to process uploaded documents.
    Uses LangChain loaders for robust text extraction.
    """

    def __init__(self, document: Document):
        self.document = document
        self.embedding_service = EmbeddingService()

        # Matches Streamlit config (1000 chars, 200 overlap)
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=1000, chunk_overlap=200, separators=["\n\n", "\n", ".", " ", ""]
        )

    def process(self) -> bool:
        """Extract text, chunk, embed, and store a document. Returns True on success."""

        try:
            logger.info(f"Processing document: {self.document.name}")
            self.document.status = Document.Status.PROCESSING
            self.document.save(update_fields=["status"])

            # 1. Load & Split (The "Streamlit Way")
            lc_docs = self._load_and_split()

            # SAFETY CHECK: If no text was found, FAIL explicitly.
            if not lc_docs:
                raise ValueError("Text extraction returned empty result. Is this a scanned PDF?")

            logger.info(f"Generated {len(lc_docs)} chunks.")

            # 2. Embed
            chunks_text = [doc.page_content for doc in lc_docs]
            vectors = self.embedding_service.embed_documents(chunks_text)

            # 3. Save to DB
            with transaction.atomic():
                self.document.chunks.all().delete()

                db_chunks = []
                max_page = 0  # Track highest page number
                for i, (lc_doc, vector) in enumerate(zip(lc_docs, vectors)):
                    page = lc_doc.metadata.get("page", 0) + 1
                    max_page = max(max_page, page)

                    db_chunks.append(
                        DocumentChunk(
                            document=self.document,
                            content=lc_doc.page_content,
                            chunk_index=i,
                            page_number=page,
                            embedding=vector,
                        )
                    )

                DocumentChunk.objects.bulk_create(db_chunks)

                self.document.status = Document.Status.COMPLETED
                self.document.chunk_count = len(db_chunks)
                self.document.page_count = max_page  # <-- ADD THIS
                self.document.processed_at = timezone.now()
                self.document.save()

            return True

        except Exception as e:
            logger.error(f"Document processing failed: {e}", exc_info=True)
            self.document.status = Document.Status.FAILED
            self.document.error_message = str(e)
            self.document.save()
            return False

    def _load_and_split(self):
        """
        Uses LangChain loaders to load and split in one go.
        """
        file_path = self.document.file.path
        ext = os.path.splitext(file_path)[1].lower()

        if ext == ".pdf":
            loader = PyPDFLoader(file_path)
        elif ext == ".docx":
            # Note: Requires `pip install docx2txt`
            loader = Docx2txtLoader(file_path)
        elif ext == ".txt":
            loader = TextLoader(file_path, encoding="utf-8")
        else:
            raise ValueError(f"Unsupported file type: {ext}")

        return loader.load_and_split(self.text_splitter)
