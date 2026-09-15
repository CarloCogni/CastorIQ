# writeback/services/proposal_serializer.py
"""One serialiser for the proposal card (spec U-1).

The HTTP view, the WebSocket consumer and the persisted-card template all
read the same dict, and one Django partial renders it, so the card cannot
drift between transports. Target evidence is read from the index; no IFC
file is opened between the sandbox result and the card (spec P-2).
"""

from __future__ import annotations

from django.db.models import Q
from django.template.loader import render_to_string

from ifc_processor.models import IFCEntity
from writeback.models import ModificationProposal

from .verifier import FLAG_LABELS, aggregate_rows, flag_rows

CARD_TEMPLATE = "writeback/components/proposal_card.html"

#: Shown per target as its one distinguishing property, first match wins.
_EVIDENCE_PREFERENCE = ("IsExternal", "LoadBearing", "FireRating", "Reference", "ObjectType")
#: Targets listed on the card; the rest is a count.
_MAX_TARGETS = 50
_IGNORED_KEY_PREFIXES = ("Type.", "ClassRef.")


def serialize_proposal(
    proposal: ModificationProposal, *, entities: dict[tuple, IFCEntity] | None = None
) -> dict:
    """The card as a plain dict: request, explanation, targets, rows, code, Guardian.

    ``entities`` is the map :func:`prefetch_target_entities` builds when a page
    renders many cards, so the index is read once per page, not once per card.
    """
    rows = flag_rows(aggregate_rows(proposal.diff or {}), proposal.request_text or "")
    targets = target_evidence(proposal, entities=entities)
    commit = proposal.git_commit if proposal.git_commit_id else None
    return {
        "id": str(proposal.id),
        "status": proposal.status,
        "status_display": proposal.get_status_display(),
        "request": proposal.request_text,
        "explanation": proposal.explanation,
        "explainer_model": proposal.explainer_model,
        "targets": targets,
        "target_count": len(proposal.target_global_ids or []),
        "targets_hidden": max(0, len(proposal.target_global_ids or []) - len(targets)),
        "rows": [
            {
                **row.as_dict(),
                "label": row.label,
                "flag_label": FLAG_LABELS.get(row.flag_reason, ""),
            }
            for row in rows
        ],
        "flagged_count": sum(1 for row in rows if row.flagged),
        "has_flagged_rows": any(row.flagged for row in rows),
        "flags_acknowledged": proposal.flags_acknowledged_at is not None,
        "code": proposal.code,
        "affected_count": proposal.affected_count,
        "conflict_ids": ",".join(str(i) for i in (proposal.linked_conflict_ids or [])),
        "guardian": {
            "status": _guardian_status(proposal),
            "result": proposal.verification_result,
            "source": proposal.verification_source,
            "skipped": proposal.guardian_skipped,
        },
        "commit_hash": commit.commit_hash[:8] if commit else "",
        "error_message": proposal.error_message,
        "is_v3": proposal.is_v3,
    }


def render_card(proposal: ModificationProposal, project, card: dict | None = None) -> str:
    """The card's HTML from the one partial; used by the live and the persisted paths."""
    return render_to_string(
        CARD_TEMPLATE,
        {"card": card or serialize_proposal(proposal), "proposal": proposal, "project": project},
    )


def prefetch_target_entities(proposals: list[ModificationProposal]) -> dict[tuple, IFCEntity]:
    """One index query for every target of every proposal on a page, keyed ``(ifc_file_id, gid)``."""
    wanted: dict = {}
    for proposal in proposals:
        for gid in list(proposal.target_global_ids or [])[:_MAX_TARGETS]:
            wanted.setdefault(proposal.ifc_file_id, set()).add(gid)
    if not wanted:
        return {}
    query = Q()
    for ifc_file_id, gids in wanted.items():
        query |= Q(ifc_file_id=ifc_file_id, global_id__in=gids)
    entities = IFCEntity.objects.filter(query).select_related("spatial_container__entity")
    return {(e.ifc_file_id, e.global_id): e for e in entities}


def target_evidence(
    proposal: ModificationProposal, *, entities: dict[tuple, IFCEntity] | None = None
) -> list[dict]:
    """Name, type, container and one distinguishing property per target, from the index.

    A target the index does not know (a class the parser does not index, such
    as a type object, or a row that was never processed) is listed by its
    GlobalId with an honest label; targets are read before ``modify`` runs, so
    none of them is ever a newly created entity.
    """
    ids = list(proposal.target_global_ids or [])[:_MAX_TARGETS]
    if not ids:
        return []
    if entities is None:
        entities = prefetch_target_entities([proposal])
    evidence = []
    for gid in ids:
        entity = entities.get((proposal.ifc_file_id, gid))
        if entity is None:
            evidence.append(
                {
                    "global_id": gid,
                    "name": "(not in the index)",
                    "ifc_type": "",
                    "container": "",
                    "evidence": "",
                }
            )
            continue
        container = entity.spatial_container.entity.name if entity.spatial_container_id else ""
        evidence.append(
            {
                "global_id": gid,
                "name": entity.name or gid,
                "ifc_type": entity.ifc_type,
                "container": container or "",
                "evidence": _distinguishing_property(entity.properties or {}),
            }
        )
    return evidence


def _guardian_status(proposal: ModificationProposal) -> str:
    """``skipped`` for a recorded skip; a check that never ran on a settled row reads ``unknown``."""
    if proposal.guardian_skipped:
        return "skipped"
    status = proposal.verification_status
    never_ran = status == ModificationProposal.VerificationStatus.PENDING
    settled = proposal.status != ModificationProposal.Status.PENDING
    return "unknown" if never_ran and settled else status


def _distinguishing_property(properties: dict) -> str:
    keys = [k for k in properties if "." in k and not k.startswith(_IGNORED_KEY_PREFIXES)]
    for preferred in _EVIDENCE_PREFERENCE:
        for key in keys:
            if key.endswith("." + preferred):
                return f"{key.split('.', 1)[1]} = {properties[key]}"
    return ""
