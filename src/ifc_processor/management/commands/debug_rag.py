import logging
from django.core.management.base import BaseCommand
from django.conf import settings
from documents.models import Document, DocumentChunk
from chat.services.rag_service import RAGService
from embeddings.services.embedding_service import EmbeddingService


class Command(BaseCommand):
    help = "Debug RAG pipeline: Check chunks, vectors, and retrieval."

    def add_arguments(self, parser):
        parser.add_argument('query', type=str, help='The test question to ask')

    def handle(self, *args, **options):
        query = options['query']

        print("\n" + "=" * 60)
        print(f"🕵️  RAG DIAGNOSTIC TOOL")
        print("=" * 60)

        # 1. CHECK CONFIG
        print(f"\n[1] CONFIGURATION")
        print(f"    • OLLAMA_HOST: {settings.OLLAMA_HOST}")
        print(f"    • MODEL (LLM): {settings.OLLAMA_MODEL}")
        print(f"    • MODEL (EMBED): {settings.OLLAMA_EMBED_MODEL}")

        # 2. CHECK EMBEDDING SERVICE
        print(f"\n[2] TESTING EMBEDDING SERVICE")
        service = EmbeddingService()
        try:
            vec = service.embed_query("test")
            print(f"    ✅ Embedding generation success.")
            print(f"    📏 Vector Dimensions: {len(vec)} (Expect 1024 for mxbai)")
            if len(vec) != 1024:
                print(f"    ⚠️  WARNING: You are using 'mxbai' but vector is {len(vec)}. DB expects 1024.")
        except Exception as e:
            print(f"    ❌ Embedding failed: {e}")
            return

        # 3. CHECK DOCUMENTS
        print(f"\n[3] CHECKING DATABASE CONTENT")
        docs = Document.objects.all()
        if not docs.exists():
            print("    ❌ No documents found in DB.")
            return

        for doc in docs:
            chunk_count = doc.chunks.count()
            print(f"    📄 Document: {doc.name} (ID: {doc.id})")
            print(f"       • Status: {doc.status}")
            print(f"       • Chunks: {chunk_count}")

            if chunk_count == 0:
                print("       ❌ ERROR: Document is 'Completed' but has 0 chunks. Text extraction failed.")
                continue

            # Inspect first chunk
            first = doc.chunks.first()
            content_preview = first.content[:50].replace('\n', ' ')
            vec_len = len(first.embedding) if first.embedding else 0

            print(f"       • Sample Chunk: '{content_preview}...'")
            print(f"       • Chunk Vector Dim: {vec_len}")

            if vec_len == 0:
                print("       ❌ ERROR: Chunk exists but has no vector.")

        # 4. TEST RETRIEVAL
        print(f"\n[4] TESTING RETRIEVAL for query: '{query}'")
        project = docs.first().project
        rag = RAGService()

        # Test Vector Search
        results = rag._retrieve_vector_context(project, query, "docs")
        print(f"    🔍 Vector Search Results: {len(results)}")
        for r in results:
            print(f"       - [Score: {r.distance:.4f}] {r.content[:60]}...")

        # Test Summary Fallback
        summary_results = rag._retrieve_summary_context(project)
        print(f"    📑 Summary Mode Results: {len(summary_results)}")

        print("\n" + "=" * 60 + "\n")