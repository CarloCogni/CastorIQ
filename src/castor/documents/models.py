"""Document models."""

from django.db import models
from pgvector.django import VectorField

from core.models import TimestampedModel
from environments.models import Environment


class Document(TimestampedModel):
    """An uploaded document (PDF, DOCX)."""

    class DocumentType(models.TextChoices):
        PDF = "pdf", "PDF"
        DOCX = "docx", "Word Document"
        OTHER = "other", "Other"

    environment = models.ForeignKey(
        Environment, on_delete=models.CASCADE, related_name="documents"
    )
    name = models.CharField(max_length=255)
    file_path = models.CharField(max_length=500)
    document_type = models.CharField(max_length=20, choices=DocumentType.choices)

    # Processing status
    is_processed = models.BooleanField(default=False)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name


class DocumentChunk(TimestampedModel):
    """A chunk of text from a document."""

    document = models.ForeignKey(
        Document, on_delete=models.CASCADE, related_name="chunks"
    )
    content = models.TextField()
    page_number = models.IntegerField(null=True)
    chunk_index = models.IntegerField()

    # Vector embedding
    embedding = VectorField(dimensions=768, null=True)

    class Meta:
        ordering = ["document", "chunk_index"]
        indexes = [
            models.Index(fields=["document", "chunk_index"]),
        ]

    def __str__(self):
        return f"{self.document.name} - Chunk {self.chunk_index}"
