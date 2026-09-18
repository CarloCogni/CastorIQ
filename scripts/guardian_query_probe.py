# scripts/guardian_query_probe.py
"""
Guardian two-query retrieval probe — read-only measurement, no proposal writes.

Investigates: writeback_V3 decision-log "review 10 — Guardian searches twice,
unions the results" (2026-09-17) and Erez's 2026-09-18 finding (TEMP 3 /
Duplex, wall Basic Wall:Exterior - Brick on Block:143590,
Pset_WallCommon.ThermalTransmittance): five otherwise-identical Guardian runs,
differing only in the proposed value, split 3 retrieved / 2 "no relevant
docs" against the same document, with no correlation to how close the
proposed value sits to the document's own threshold value.

Two candidate mechanisms:
  (a) build_request_query carries the proposed value as literal text, so the
      request-text query's embedding moves with it.
  (b) the union of two queries in _search_documents orders or scores the
      candidate set differently than one query did.

For each given proposal id, this script:
  1. Calls build_guardian_query, build_request_query and dominant_row from
     guardian_service.py directly — the real functions, not a reimplementation.
  2. Prints the runtime type of the dominant DiffRow's `after` value. The
     diff-row query can only carry the proposed value when `row.after` is a
     `str` (guardian_service.py, build_guardian_query: `if isinstance(row.after,
     str) and row.after: parts.append(row.after)`) — for a numeric property
     like ThermalTransmittance, `after` is a Python float, so the diff-row
     query is architecturally identical across all five runs. This makes the
     float-vs-str fact visible in the output instead of asserted.
  3. Embeds the diff-row query, the request-text query, and the request-text
     query with every numeric literal stripped, and reports each one's
     cosine distance to a target chunk (--chunk-contains identifies it, e.g.
     a snippet of clause 2.1) plus whether it passes RELEVANCE_THRESHOLD.
  4. Calls GuardianService._search_documents(project, [diff_query,
     request_query]) unmodified, to show the actual top-k union the real
     pipeline would have produced for that proposal.

Usage:
    uv run python scripts/guardian_query_probe.py \
        --proposal <uuid> [--proposal <uuid> ...] \
        --chunk-contains "0.30"

No writes. No proposals created. No model call beyond the embedding calls
already made read-only above (GuardianService is constructed but .check()
is never called, so nothing is saved).
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")

import django  # noqa: E402

django.setup()

from pgvector.django import CosineDistance  # noqa: E402

from documents.models import DocumentChunk  # noqa: E402
from embeddings.services.embedding_service import EmbeddingService  # noqa: E402
from writeback.models import ModificationProposal  # noqa: E402
from writeback.services.guardian_service import (  # noqa: E402
    GuardianService,
    build_guardian_query,
    build_request_query,
    dominant_row,
)

NUMERIC_LITERAL = re.compile(r"\d+(?:\.\d+)?")


def strip_numbers(text: str) -> str:
    """The request-text query with every numeric literal removed."""
    return NUMERIC_LITERAL.sub("", text).strip()


def cosine_distance_to_chunk(
    embedder: EmbeddingService, query: str, chunk: DocumentChunk
) -> float | None:
    """The same CosineDistance _search_documents uses, computed against one known chunk."""
    if not query:
        return None
    vector = embedder.embed_query(query)
    if not vector:
        return None
    return (
        DocumentChunk.objects.filter(pk=chunk.pk)
        .annotate(distance=CosineDistance("embedding", vector))
        .values_list("distance", flat=True)
        .first()
    )


def find_target_chunk(project, contains: str) -> DocumentChunk | None:
    """The chunk under investigation, located by a text snippet (e.g. clause 2.1's own words)."""
    return (
        DocumentChunk.objects.filter(document__project=project, content__icontains=contains)
        .select_related("document")
        .first()
    )


def probe(proposal_id: str, chunk_contains: str, threshold: float) -> None:
    """Print the query reconstruction and measured distances for one proposal."""
    proposal = ModificationProposal.objects.select_related("ifc_file", "ifc_file__project").get(
        pk=proposal_id
    )
    project = proposal.ifc_file.project

    row = dominant_row(proposal)
    diff_query = build_guardian_query(proposal)
    request_query = build_request_query(proposal)
    stripped_query = strip_numbers(request_query)

    print(f"\n=== Proposal {proposal.id} ===")
    print(f"request_text        : {proposal.request_text!r}")
    if row is not None:
        print(f"dominant_row.after   : {row.after!r}  (type: {type(row.after).__name__})")
    else:
        print("dominant_row         : None (query falls back to explanation/request text)")
    print(f"diff-row query       : {diff_query!r}")
    print(f"request-text query   : {request_query!r}")
    print(f"stripped-value query : {stripped_query!r}")

    chunk = find_target_chunk(project, chunk_contains)
    if chunk is None:
        print(f"!! No chunk found containing {chunk_contains!r} in this project's documents.")
        return
    print(f"target chunk         : {chunk.document.name}, p.{chunk.page_number} (id={chunk.id})")

    embedder = EmbeddingService()
    queries = [
        ("diff-row query", diff_query),
        ("request-text query", request_query),
        ("request-text query, value stripped", stripped_query),
    ]
    print(f"\n{'query':38} {'cosine distance':>16} {'passes threshold':>18}")
    for label, query in queries:
        distance = cosine_distance_to_chunk(embedder, query, chunk)
        passes = "yes" if distance is not None and distance <= threshold else "no"
        distance_str = f"{distance:.4f}" if distance is not None else "n/a (empty query)"
        print(f"{label:38} {distance_str:>16} {passes:>18}")

    guardian = GuardianService()
    found = guardian._search_documents(project, [diff_query, request_query])
    print(f"\n_search_documents() union result: {len(found)} chunk(s) above threshold")
    for c in found:
        print(f"  - {c.document.name}, p.{c.page_number} (id={c.id}, distance={c.distance:.4f})")


def main() -> None:
    """Parse args and run the probe for each given proposal id."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--proposal", action="append", required=True, dest="proposals", help="Proposal UUID."
    )
    parser.add_argument(
        "--chunk-contains",
        required=True,
        help="Substring identifying the target chunk, e.g. a snippet of clause 2.1's own text.",
    )
    parser.add_argument("--threshold", type=float, default=GuardianService.RELEVANCE_THRESHOLD)
    args = parser.parse_args()

    for proposal_id in args.proposals:
        probe(proposal_id, args.chunk_contains, args.threshold)


if __name__ == "__main__":
    main()
