# documents/tests/factories.py
"""Factory Boy factories for documents models."""

import factory
from django.core.files.uploadedfile import SimpleUploadedFile

from environments.tests.factories import ProjectFactory


class DocumentFactory(factory.django.DjangoModelFactory):
    """Factory for documents.Document."""

    class Meta:
        model = "documents.Document"

    project = factory.SubFactory(ProjectFactory)
    name = factory.Sequence(lambda n: f"document_{n}.pdf")
    file = factory.LazyFunction(
        lambda: SimpleUploadedFile(
            "test_doc.pdf",
            b"%PDF-1.4 test document content",
            content_type="application/pdf",
        )
    )
    document_type = "pdf"
    status = "completed"
    chunk_count = 0
    page_count = 1


class DocumentChunkFactory(factory.django.DjangoModelFactory):
    """Factory for documents.DocumentChunk."""

    class Meta:
        model = "documents.DocumentChunk"

    document = factory.SubFactory(DocumentFactory)
    content = factory.Sequence(
        lambda n: f"This is chunk content number {n}. It contains useful text."
    )
    chunk_index = factory.Sequence(lambda n: n)
    page_number = 1
    embedding = None
