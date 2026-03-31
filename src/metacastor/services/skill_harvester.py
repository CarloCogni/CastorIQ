# metacastor/services/skill_harvester.py
"""
Post-commit hook that creates SkillExample records from successful proposals.

Called by modification_service.execute() after a proposal reaches APPLIED status.
The entire body is wrapped in try/except — this hook must NEVER propagate an
exception or block the commit flow.

Organic harvest condition: was_approved=True AND commit_success=True.
"""

import logging

from embeddings.services.embedding_service import EmbeddingService
from metacastor.models import SkillExample
from metacastor.services.entity_type_extractor import extract_entity_types
from writeback.models import ModificationProposal

logger = logging.getLogger(__name__)


def harvest_skill_example(proposal: ModificationProposal, commit_success: bool) -> None:
    """
    Create a SkillExample from a completed modification proposal.

    Args:
        proposal:       The ModificationProposal that was just applied.
        commit_success: True if the IFC write and Git commit succeeded.

    Never raises. All exceptions are caught and logged as warnings.
    """
    try:
        _harvest(proposal, commit_success)
    except Exception:
        logger.warning(
            "Skill harvest failed for proposal %s — skipping. Pipeline unaffected.",
            proposal.id,
            exc_info=True,
        )


def _harvest(proposal: ModificationProposal, commit_success: bool) -> None:
    """Inner harvest logic — may raise; caller wraps in try/except."""
    query = proposal.request_text
    was_approved = proposal.reviewed_by is not None

    # Only store examples that cleared the approval gate.
    if not (was_approved and commit_success):
        logger.debug(
            "Proposal %s skipped harvest (approved=%s, commit_success=%s).",
            proposal.id,
            was_approved,
            commit_success,
        )
        return

    embedding = EmbeddingService().embed_query(query)
    entity_types = extract_entity_types(query)

    # Determine outcome tier from the proposal's intent.
    intent = proposal.intent_json or {}
    if isinstance(intent, list):
        outcome_tier = intent[0].get("tier", 1) if intent else 1
    else:
        outcome_tier = intent.get("tier", 1)

    # Capture generated code for Tier 3 proposals.
    generated_code: str | None = None
    if outcome_tier == 3 and isinstance(intent, dict):
        generated_code = intent.get("code") or None

    SkillExample.objects.create(
        project=proposal.project if hasattr(proposal, "project") else None,
        query_text=query,
        query_embedding=embedding,
        intent_json=proposal.intent_json or {},
        entity_types=entity_types,
        outcome_tier=outcome_tier,
        was_approved=True,
        commit_success=True,
        is_organic=True,
        generated_code=generated_code,
    )

    logger.debug(
        "Harvested skill example from proposal %s (tier=%d, types=%s).",
        proposal.id,
        outcome_tier,
        entity_types,
    )
