# documents/models.py
"""Document models - PDF/DOCX processing."""

from django.db import models
from pgvector.django import VectorField

from core.models import UUIDModel
from environments.models import Project


def document_upload_path(instance, filename):
    """Generate upload path for documents."""
    return f"projects/{instance.project_id}/documents/{filename}"


class Document(UUIDModel):
    """An uploaded document (PDF, DOCX) within a project."""

    class DocumentType(models.TextChoices):
        PDF = "pdf", "PDF"
        DOCX = "docx", "Word Document"
        TXT = "txt", "Text File"
        SCANNED_PDF = "scanned_pdf", "Scanned PDF"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    class OcrStatus(models.TextChoices):
        NONE = "none", "None"
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        DONE = "done", "Done"
        FAILED = "failed", "Failed"

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="documents",
        verbose_name="Project",
    )
    name = models.CharField(
        max_length=1_500,
        db_index=True,
        verbose_name="File Name",
    )
    file = models.FileField(
        upload_to=document_upload_path,
        verbose_name="File",
        max_length=500,
        help_text="Supported formats: PDF, DOCX, TXT",
    )
    document_type = models.CharField(
        max_length=20,
        choices=DocumentType.choices,
        db_index=True,
        verbose_name="Document Type",
    )

    # Processing status
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
        verbose_name="Status",
    )
    processed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Processed At",
    )
    error_message = models.TextField(
        blank=True,
        verbose_name="Error Message",
        help_text="Error details if processing failed",
    )

    # Statistics
    chunk_count = models.PositiveIntegerField(
        default=0,
        verbose_name="Chunk Count",
        help_text="Number of text chunks extracted",
    )
    page_count = models.PositiveIntegerField(
        default=0,
        verbose_name="Page Count",
    )

    # OCR metadata
    has_visual_content = models.BooleanField(
        default=False,
        verbose_name="Has Visual Content",
        help_text="Set when initial ingestion detects image-dominant or zero-text pages",
    )
    ocr_status = models.CharField(
        max_length=20,
        choices=OcrStatus.choices,
        default=OcrStatus.NONE,
        db_index=True,
        verbose_name="OCR Status",
    )
    ocr_processed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="OCR Processed At",
    )
    ocr_page_count = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name="OCR Page Count",
        help_text="Number of pages processed by OCR",
    )
    ocr_engine = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        verbose_name="OCR Engine",
        help_text="Engine identifier, e.g. 'glm-ocr:latest'",
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Document"
        verbose_name_plural = "Documents"
        indexes = [
            models.Index(fields=["project", "status"]),
            models.Index(fields=["project", "document_type"]),
            models.Index(fields=["project", "-created_at"]),
        ]

    def __str__(self):
        return self.name


class DocumentChunk(UUIDModel):
    """A chunk of text from a document for RAG."""

    class ChunkSource(models.TextChoices):
        TEXT_PARSER = "text_parser", "Text Parser"
        GLM_OCR = "glm_ocr", "GLM-OCR"
        HYBRID = "hybrid", "Hybrid"

    document = models.ForeignKey(
        Document,
        on_delete=models.CASCADE,
        related_name="chunks",
        verbose_name="Document",
    )
    content = models.TextField(
        verbose_name="Content",
        help_text="Text content of this chunk",
    )

    # Location info
    page_number = models.PositiveIntegerField(
        null=True,
        blank=True,
        db_index=True,
        verbose_name="Page Number",
    )
    chunk_index = models.PositiveIntegerField(
        db_index=True,
        verbose_name="Chunk Index",
        help_text="Sequential index of this chunk within the document",
    )

    # Character positions for highlighting
    start_char = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name="Start Character",
    )
    end_char = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name="End Character",
    )

    # Provenance
    chunk_source = models.CharField(
        max_length=20,
        choices=ChunkSource.choices,
        default=ChunkSource.TEXT_PARSER,
        db_index=True,
        verbose_name="Chunk Source",
    )
    extracted_from_image = models.BooleanField(
        default=False,
        verbose_name="Extracted from Image",
        help_text="True when this chunk was produced by OCR from a raster image",
    )

    # Vector embedding
    embedding = VectorField(
        dimensions=1024,  # mxbai-embed-large
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ["document", "chunk_index"]
        verbose_name = "Document Chunk"
        verbose_name_plural = "Document Chunks"
        unique_together = ["document", "chunk_index"]
        indexes = [
            models.Index(fields=["document", "chunk_index"]),
            models.Index(fields=["document", "page_number"]),
        ]

    def __str__(self):
        return f"{self.document.name} - Chunk {self.chunk_index}"
