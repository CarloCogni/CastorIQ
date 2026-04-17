# metacastor/services/skill_retriever.py
"""
Two-stage skill retrieval for few-shot injection into the IntentClassifier.

Stage 1 — Entity type pre-filter (Python-level hard gate):
    Narrows the candidate pool to examples whose entity_types overlap with
    the types detected in the current query. Prevents cross-domain poisoning.

Stage 2 — Cosine similarity ranking (pgvector):
    Within the filtered pool, ranks by embedding similarity and returns top-K.

Design constraints:
  - Only retrieves examples where was_approved=True AND commit_success=True.
  - K=3 hardcoded — no dynamic K.
  - Returns truncated dicts (operation, filter, tier, confidence, query_text,
    generated_code) to minimise token cost when injected into the system prompt.
    `generated_code` is None for Tier 1/2 examples and carries the successful
    IfcOpenShell code for Tier 3 examples (Deliverable 5 — certified Tier 3
    reuse). The Tier 3 planner filters on this field when building few-shot
    reference patterns.
  - Falls back to [] on any error — never blocks the pipeline.
"""

import json
import logging

from pgvector.django import CosineDistance

from embeddings.services.embedding_service import EmbeddingService
from metacastor.models import SkillExample
from metacastor.services.entity_type_extractor import extract_entity_types

logger = logging.getLogger(__name__)

# Hard cap — never inject more than 3 examples.
_K = 3

# How many candidates to fetch from DB before Python-level entity type filter.
_CANDIDATE_POOL = 50


def retrieve(user_message: str, project=None, k: int = _K) -> list[dict]:
    """
    Retrieve top-k approved skill examples semantically similar to user_message.

    Args:
        user_message: Raw user input (pre-classification).
        project:      Project instance for future scoped retrieval (unused in D2).
        k:            Maximum examples to return. Defaults to K=3.

    Returns:
        List of truncated intent dicts ready for injection. Empty list if no
        suitable examples exist or on any retrieval error.
    """
    # Guard: skip retrieval entirely if the bank is empty.
    if not SkillExample.objects.filter(was_approved=True, commit_success=True).exists():
        logger.debug("Skill bank empty — skipping retrieval.")
        return []

    try:
        query_vector = EmbeddingService().embed_query(user_message)
    except Exception:
        logger.warning("Embedding failed during skill retrieval — skipping injection.")
        return []

    # Fetch candidate pool: most recent approved+committed examples with embeddings.
    candidates = list(
        SkillExample.objects.filter(
            was_approved=True,
            commit_success=True,
            query_embedding__isnull=False,
        ).order_by("-created_at")[:_CANDIDATE_POOL]
    )

    # Stage 1 — Python-level entity type pre-filter.
    target_types = extract_entity_types(user_message)
    if target_types:
        candidates = [ex for ex in candidates if any(t in ex.entity_types for t in target_types)]
        logger.debug(
            "Entity type filter (%s): %d candidates remaining.",
            target_types,
            len(candidates),
        )

    if not candidates:
        return []

    # Stage 2 — Cosine similarity ranking via pgvector.
    candidate_ids = [ex.pk for ex in candidates]
    ranked = (
        SkillExample.objects.filter(pk__in=candidate_ids)
        .annotate(distance=CosineDistance("query_embedding", query_vector))
        .order_by("distance")[:k]
    )

    return [_truncate(ex) for ex in ranked]


def _truncate(example: SkillExample) -> dict:
    """
    Return a minimal dict for few-shot injection.

    Only the fields that guide the LLM's output format are included.
    Full intent_json is excluded to conserve tokens.
    """
    intent = example.intent_json
    # Handle chained intents (list) — use first element for the example header.
    if isinstance(intent, list):
        intent = intent[0] if intent else {}

    return {
        "query_text": example.query_text,
        "operation": intent.get("operation", ""),
        "filter": json.dumps(intent.get("filter", {})),
        "tier": intent.get("tier", example.outcome_tier),
        "confidence": intent.get("confidence", ""),
        "generated_code": example.generated_code,
    }
