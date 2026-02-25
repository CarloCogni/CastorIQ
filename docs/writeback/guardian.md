# Guardian — Retrieval-Augmented Verification (RAV)

The Guardian is Castor's document-aware verification layer. Before any modification proposal is presented for user approval, the Guardian cross-references the proposed change against the project's uploaded specification documents.

**Guardian advises — it never blocks.** The entire check runs inside a try/except in `ModificationService`. A failure is logged as a warning and the proposal is still created.

## How It Works

```
ModificationProposal created
    │
    ▼
Build search query from intent
    (entity type + property name + value → natural language)
    │
    ▼
Semantic search over DocumentChunks
    (pgvector cosine distance, threshold ≤ 0.45)
    │
    ├── No relevant chunks found → verdict: UNKNOWN
    │
    └── Relevant chunks found
            │
            ▼
        LLM evaluation
            (system prompt + proposal details + document excerpts)
            │
            ▼
        Verdict: CONFIRMED / CONFLICT / NO_INFO
```

## Verdicts

| Verdict | Meaning | Proposal field | Example |
|---|---|---|---|
| **CONFIRMED** | Document explicitly supports the proposed value | `verification_status = "verified"` | Proposal: FireRating=EI120. Doc: "minimum EI120 for all external walls" |
| **CONFLICT** | Document explicitly states a different value | `verification_status = "conflict"` | Proposal: FireRating=EI120. Doc: "external walls shall be rated EI90" |
| **NO_INFO** | Excerpts don't mention anything relevant | `verification_status = "unknown"` | Proposal: AcousticRating=52dB. Doc: only discusses fire ratings |
| *(no chunks)* | No relevant documents found at all | `verification_status = "unknown"` | No project documents uploaded |
| *(exception)* | Guardian check failed | `verification_status = "failed"` | Embedding service down, LLM timeout, etc. |

## Service: `GuardianService`

### Configuration

| Setting | Value | Notes |
|---|---|---|
| `RELEVANCE_THRESHOLD` | 0.45 | Cosine distance cutoff — chunks with distance > 0.45 are excluded |
| LLM temperature | 0.1 | Low temperature for consistent, conservative verdicts |
| LLM format | `json` | Forces structured JSON output |
| Top-K | 5 | Maximum document chunks retrieved per query |

### Search Query Construction (`_build_search_query`)

Builds a natural language query from the proposal's `intent_json`:

1. **Entity type:** Strips "Ifc" prefix, lowercases → e.g. `"IfcWall"` → `"wall"`
2. **Property name:** CamelCase to words → e.g. `"FireRating"` → `"fire rating"`
3. **New value:** Included as-is if it's a string
4. **Pset hint:** If pset contains "Common", adds "requirements"
5. **Fallback:** If no parts could be extracted, uses `proposal.explanation` or `proposal.request_text`

Example: intent `{filter: {ifc_type: "IfcWall"}, property: "FireRating", new_value: "EI120", pset: "Pset_WallCommon"}` → query: `"wall fire rating EI120 requirements"`

### Document Search (`_search_documents`)

1. Embeds the query via `EmbeddingService.embed_query()`
2. Queries `DocumentChunk` model with pgvector's `CosineDistance` annotation
3. Filters to chunks from completed documents in the same project
4. Orders by distance ascending, takes top 5
5. Filters to chunks with `distance ≤ RELEVANCE_THRESHOLD` (0.45)

### LLM Evaluation (`_evaluate`)

Sends a structured prompt to the LLM with:

- **Entity type** from the intent filter
- **Operation** name
- **Property or attribute** (formatted as `Pset.Property` or just the attribute name)
- **New value**
- **Proposal explanation**
- **Document excerpts** — each formatted as `[DocumentName, Page N]\n{content}`, separated by `---`

The LLM returns:
```json
{
  "verdict": "CONFIRMED | CONFLICT | NO_INFO",
  "explanation": "1-2 sentence summary",
  "source_detail": "Fire Strategy.pdf, p.14"
}
```

If the LLM returns invalid JSON, the verdict defaults to `NO_INFO` with an error explanation.

## Proposal Fields Updated

After the check, three fields are written to the `ModificationProposal`:

| Field | Content |
|---|---|
| `verification_status` | One of: `pending`, `verified`, `conflict`, `unknown`, `failed` |
| `verification_result` | The LLM's explanation text |
| `verification_source` | Document citation string (e.g. "Fire Strategy.pdf, p.14") |

## Design Decisions

- **Non-blocking:** Guardian failures don't prevent proposal creation. The check is wrapped in try/except with a warning log. This keeps the modification flow reliable even when the embedding service or LLM is down.
- **Conservative verdicts:** The system prompt instructs the LLM to only flag CONFLICT when there's a clear contradiction. Ambiguous or tangentially related information should return NO_INFO.
- **Most specific excerpt wins:** When multiple excerpts are relevant, the LLM is instructed to base its verdict on the most specific one.
- **User retains authority:** The verdict is displayed alongside the approval interface but does not affect the approve/reject buttons. A CONFLICT verdict is informational — the user decides.

## Dependencies

| Service | Used for |
|---|---|
| `EmbeddingService` | Generating query embeddings for semantic search |
| `DocumentChunk` model | Source of document text + embeddings (from the `documents` app) |
| `ChatOllama` | LLM evaluation of excerpts against the proposal |
| pgvector | Cosine distance similarity search |
