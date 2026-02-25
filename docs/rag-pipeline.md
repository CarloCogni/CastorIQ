# RAG Pipeline

The Retrieval-Augmented Generation (RAG) pipeline powers Castor's Ask mode. It enables natural language queries across both IFC model data and project documents, returning grounded responses with source citations.

## Why RAG

RAG is chosen over fine-tuning for three reasons:

1. **Grounded responses** — answers are based on verifiable project data, not parametric memory
2. **No retraining** — when project documents change, only the vector store is updated
3. **Source attribution** — every answer can cite its sources (specific entities, document pages)

## Architecture Overview

The pipeline has two phases: **indexing** (offline, at upload time) and **retrieval + generation** (online, at query time).
```
                        INDEXING (upload time)
                        ─────────────────────

  IFC File ──→ Parse ──→ Extract Entities ──→ Semantic Description ──→ Embed ──→ pgvector
  Document ──→ Extract Text ──→ Chunk ──→ Embed ──────────────────────────────→ pgvector

                             ▲ shared 1024d vector space ▲


                     RETRIEVAL + GENERATION (query time)
                     ──────────────────────────────────

  User Query ──→ Embed ──→ Similarity Search (IFC entities + document chunks)
                         ──→ Rank and Assemble Context
                         ──→ LLM Generates Response
                         ──→ Return with Source Citations
```

## Indexing Phase

### IFC Entity Indexing

Handled by the IFC Processor (see [ifc-processor.md](ifc-processor.md)). Each entity's semantic description is embedded and stored as a vector. The description uses natural language so that user queries have high similarity with relevant entities.

### Document Indexing
```
PDF/DOCX ──→ Text Extraction (PyMuPDF) ──→ Chunking ──→ Embedding ──→ pgvector
```

**Text extraction:** Page-by-page extraction preserving section structure where possible. Metadata captured per page: source document, page number, detected section heading.

**Chunking strategy:**

- Target chunk size: ~500 tokens
- Overlap: ~50 tokens between consecutive chunks
- Chunk boundaries respect paragraph breaks where possible
- Each chunk retains metadata: source document, page number, section heading

**Why overlapping chunks:** Prevents information loss at chunk boundaries. A requirement that spans two pages is captured by at least one chunk in full.

### Unified Vector Space

Both IFC entity descriptions and document chunks are embedded using the same model into the same 1024-dimensional vector space. This is the key design choice that enables cross-domain retrieval: a query about "fire-rated doors on Level 1" retrieves both the relevant IFC door entities and the corresponding paragraphs from the fire safety report.

## Retrieval Phase

### Query Embedding

The user's natural language question is embedded using the same model (mxbai-embed-large) to produce a 1024d query vector.

### Similarity Search

The query vector is compared against all stored vectors (IFC entities + document chunks) using cosine similarity via pgvector. Results are ranked by similarity score.

**Filtering options:**

- Search IFC entities only (for model-specific questions)
- Search documents only (for specification questions)
- Search both (default — for cross-domain questions like "does the model match the spec?")
- Scope to a specific project / IFC file / document

### Context Assembly

Top-k results are assembled into a structured context for the LLM. The context distinguishes between IFC entities and document chunks so the LLM can reason about and cite each source type appropriately.
```
--- IFC ENTITIES ---
[1] IfcDoor "D-01" on Level 1. Fire rating: EI30. ...
[2] IfcDoor "D-02" on Level 1. Fire rating: EI30. ...

--- DOCUMENT EXCERPTS ---
[3] Fire Safety Report, p.12: "All doors in escape corridors shall be rated EI90..."
[4] Fire Safety Report, p.14: "Level 1 corridor doors require..."
```

### Result Limit and Relevance

- **Top-k:** configurable, default likely 5–10 results
- **Minimum similarity threshold:** results below a configurable score are excluded to prevent irrelevant context from polluting the LLM prompt
- **Context window budget:** total context must fit within the LLM's token limit alongside the system prompt and user query

## Generation Phase

The assembled context plus the user's query are passed to the LLM (llama3.1:8b) with a system prompt that instructs it to:

1. Answer based only on the provided context
2. Cite sources using the reference numbers from context assembly
3. Acknowledge when the context is insufficient rather than hallucinating
4. Distinguish between IFC model data and document requirements in the response

### Prompt Structure
```
System: You are Castor, an assistant for BIM projects. Answer based
        on the provided context. Cite sources. If the context doesn't
        contain enough information, say so.

Context: [assembled IFC entities + document chunks]

User: [question]
```

### Source Citations

Responses include citations back to specific IFC entities (by name and GlobalId) and document chunks (by document name and page number), allowing the user to verify the answer.

## Embedding Model

| Property | Value |
|---|---|
| Model | mxbai-embed-large via Ollama |
| Dimensions | 1024 |
| Storage | pgvector extension in PostgreSQL |
| Runs | Locally (no external API calls) |

## LLM

| Property | Value |
|---|---|
| Model | llama3.1:8b via Ollama |
| Context window | 8K tokens |
| Temperature | Low for factual queries, moderate for explanations |
| Runs | Locally (privacy-first) |

## Design Decisions

1. **Shared vector space for IFC + documents** — enables cross-domain retrieval in a single query. The alternative (separate vector stores with separate searches) would require merging and re-ranking results, adding complexity.
2. **Semantic descriptions over raw JSON** — natural language descriptions bridge the vocabulary gap between how users ask questions and how IFC data is structured.
3. **Structured context with source types** — separating IFC entities and document chunks in the prompt helps the LLM distinguish between "what the model says" and "what the spec requires," which is critical for conflict detection.
4. **Local-only inference** — no project data leaves the user's machine. This is a hard design constraint driven by data sovereignty requirements in AEC projects.
5. **pgvector over dedicated vector DB** — keeps the stack simple (one database for everything). PostgreSQL handles both relational queries and vector similarity. Trade-off: less performant at scale than Pinecone/Weaviate, but sufficient for project-sized datasets.