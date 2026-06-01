# islam/scheduling/services/utils.py
"""Shared utilities for scheduling services."""

from __future__ import annotations

import logging
from datetime import date

logger = logging.getLogger(__name__)


def get_project_data_date(project_id: str) -> tuple[date, bool]:
    """Return ``(as_of_date, is_real_data_date)`` for *project_id*.

    Looks up the most recent ScheduleSource that carries a P6 DataDate.
    If found, returns that date (progress was recorded as-of that point).
    Falls back to ``date.today()`` for CSV/Excel imports or projects that
    have never had a P6 XML import — ``is_real_data_date`` is ``False``
    in that case, meaning metrics are as-of the current system date.
    """
    from islam.scheduling.models import ScheduleSource

    source = (
        ScheduleSource.objects.filter(
            project_id=project_id,
            data_date__isnull=False,
        )
        .order_by("-imported_at")
        .only("data_date")
        .first()
    )
    if source and source.data_date:
        return source.data_date, True
    return date.today(), False
