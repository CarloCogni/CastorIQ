# facilities/services/spatial_lookup.py
"""Cheap fuzzy resolver for SPACE entities by name.

The Occupant Portal needs to turn ``"Meeting room 3-B is cold"`` into an
:class:`ifc_processor.IFCSpatialElement` row. Two design choices, both
deliberate:

* **icontains + token-overlap scoring**, no ``pg_trgm`` extension. The
  search space is tens to low hundreds of SPACE rows per project; trigrams
  would help precision later but are out of scope for M4.
* **assigned-space prior**: when scores tie, the occupant's own seating
  outranks unrelated matches. Prevents the "Meeting Room 3-B" message from
  resolving to "Meeting Room 3-A" just because alphabetical ordering
  surfaced it first.

The resolver returns a list of candidates; the LLM picks one and the
review modal lets the occupant override. Hallucinated IDs are caught by
:meth:`OccupantIntakeService` before persistence.
"""

from __future__ import annotations

import logging
import re

from django.db.models import Q

from ifc_processor.models import IFCSpatialElement

logger = logging.getLogger(__name__)


# Match minimal alphanumeric tokens — drops single chars and punctuation
# that would otherwise blow up the OR query. "3-B" splits into "3" + "B"
# (both dropped on the len>=2 floor); "Meeting" stays. The hyphen variant
# survives via the original lower-cased query (covered by the icontains
# clause on the literal substring).
_TOKEN_RE = re.compile(r"[a-z0-9]+", flags=re.IGNORECASE)
_MIN_TOKEN_LEN = 2
_DEFAULT_TOP_N = 5


def resolve_space_candidates(
    project,
    query: str,
    *,
    assigned_space=None,
    top: int = _DEFAULT_TOP_N,
) -> list[IFCSpatialElement]:
    """Return the top-N SPACE candidates for ``query`` in ``project``.

    Args:
        project: ``environments.Project`` to scope the search to.
        query: free text from the occupant.
        assigned_space: optional :class:`IFCSpatialElement` used as a
            tiebreaker prior — wins on score ties.
        top: cap on candidate count.

    The result list is ordered: highest score first, with the
    assigned-space prior winning ties. May be empty when no SPACE rows
    exist for the project.
    """
    cleaned = (query or "").strip().lower()
    qs = IFCSpatialElement.objects.filter(
        ifc_file__project=project,
        spatial_type="space",
    ).select_related("entity")

    tokens = _extract_tokens(cleaned)
    if not tokens and not cleaned:
        # Nothing to search with — still return assigned space if present
        # so the caller can default-select it.
        if assigned_space is not None:
            return [assigned_space]
        return []

    # Pre-filter by any token match. Empty tokens fall back to the cleaned
    # query as a single icontains so substrings like "3-b" still match.
    or_filter = Q()
    if tokens:
        for tok in tokens:
            or_filter |= Q(entity__name__icontains=tok) | Q(long_name__icontains=tok)
    if cleaned:
        or_filter |= Q(entity__name__icontains=cleaned) | Q(long_name__icontains=cleaned)
    candidates = list(qs.filter(or_filter).distinct()[: top * 4])

    # Make sure the assigned space is in the pool even when its name didn't
    # match the message. The prior should still surface as a fallback.
    if assigned_space is not None and assigned_space not in candidates:
        candidates.append(assigned_space)

    if not candidates:
        return []

    assigned_pk = assigned_space.pk if assigned_space is not None else None
    scored = [
        (
            _score(candidate, tokens, cleaned),
            candidate.pk == assigned_pk,
            candidate,
        )
        for candidate in candidates
    ]
    # Higher score first; on ties, assigned-space prior wins; then PK for
    # determinism.
    scored.sort(key=lambda row: (-row[0], not row[1], str(row[2].pk)))
    return [row[2] for row in scored[:top]]


def _extract_tokens(query: str) -> list[str]:
    """Lower-cased alphanumeric tokens of length ≥ 2."""
    return [
        match.group(0).lower()
        for match in _TOKEN_RE.finditer(query)
        if len(match.group(0)) >= _MIN_TOKEN_LEN
    ]


def _score(spatial: IFCSpatialElement, tokens: list[str], cleaned: str) -> int:
    """Token-overlap score against the spatial node's long_name + entity.name.

    Each matching token contributes 2; a full ``cleaned`` substring match
    contributes a +1 bonus so long contiguous hits edge out scattered
    partials. Lower-bounded at zero.
    """
    entity = getattr(spatial, "entity", None)
    haystack = " ".join(
        str(part).lower()
        for part in (
            (entity.name if entity else "") or "",
            getattr(spatial, "long_name", "") or "",
        )
    )
    if not haystack.strip():
        return 0
    score = sum(1 for tok in tokens if tok in haystack) * 2
    if cleaned and cleaned in haystack:
        score += 1
    return score
