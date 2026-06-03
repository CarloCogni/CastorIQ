# facilities/services/explore_phase_service.py
"""Per-project phase palette management for the Explore module.

Phases are the named room-state labels (Construction / Fit-out / Occupied
by default) that drive pin colours on the floor plan. Each project owns
its own palette; the first access seeds the three defaults so a brand-new
project still shows colours when the iframe loads.

The sync flow is push-from-client: the iframe owns the working list, and
:func:`sync_phases_for_project` reconciles the supplied list against the
DB so add / rename / recolour / delete all flow through one call.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from django.db import transaction

from environments.models import Project
from facilities.models.explore import DEFAULT_PHASES, ExplorePhase

logger = logging.getLogger(__name__)


def ensure_default_phases(project: Project) -> list[ExplorePhase]:
    """Seed Construction / Fit-out / Occupied if the project has no phases yet.

    Idempotent: a project that already has any phase is left untouched
    (the user may have renamed / recoloured the defaults).
    """
    if ExplorePhase.objects.filter(project=project).exists():
        return list(ExplorePhase.objects.filter(project=project))
    created = [
        ExplorePhase.objects.create(
            project=project,
            name=name,
            color=color,
            position=position,
        )
        for name, color, position in DEFAULT_PHASES
    ]
    logger.info("Seeded %d default explore phases for project %s", len(created), project.pk)
    return created


def list_for_project(project: Project) -> list[ExplorePhase]:
    """Return the project's phase list, seeding defaults on first access."""
    ensure_default_phases(project)
    return list(ExplorePhase.objects.filter(project=project))


def serialize_phases(phases: Iterable[ExplorePhase]) -> list[dict]:
    """Return the phase list in the shape Pavla's iframe expects.

    Pavla's state stores ``phases`` as a plain string list plus a
    ``phaseColors`` dict. We return both so the host can pass them in
    SET_USER_STATE without reshaping on the client.
    """
    items = list(phases)
    return [{"name": p.name, "color": p.color, "position": p.position} for p in items]


@transaction.atomic
def sync_phases_for_project(
    project: Project,
    payload_phases: list[str] | None,
    phase_colors: dict[str, str] | None,
) -> list[ExplorePhase]:
    """Reconcile the project's phase list against a client-supplied snapshot.

    ``payload_phases`` is the ordered name list from the iframe state;
    ``phase_colors`` is the override-colour dict. Missing names get
    deleted (any points referencing them have ``phase`` cleared via the
    ``SET_NULL`` rule on :class:`ExplorePoint.phase`).
    """
    if payload_phases is None:
        return list_for_project(project)

    ensure_default_phases(project)
    existing = {p.name: p for p in ExplorePhase.objects.filter(project=project)}
    seen: set[str] = set()
    colors = phase_colors or {}

    for position, name in enumerate(payload_phases):
        clean = (name or "").strip()
        if not clean:
            continue
        seen.add(clean)
        color = colors.get(clean, "#8a8da6")
        if clean in existing:
            phase = existing[clean]
            updated = False
            if phase.position != position:
                phase.position = position
                updated = True
            if color and phase.color != color:
                phase.color = color
                updated = True
            if updated:
                phase.save(update_fields=["position", "color", "updated_at"])
        else:
            ExplorePhase.objects.create(
                project=project,
                name=clean,
                color=color,
                position=position,
            )

    stale = [phase for name, phase in existing.items() if name not in seen]
    for phase in stale:
        phase.delete()
    return list(ExplorePhase.objects.filter(project=project))
