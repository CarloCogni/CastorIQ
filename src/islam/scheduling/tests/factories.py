# islam/scheduling/tests/factories.py
"""Factory Boy factories for islam scheduling models."""

from __future__ import annotations

import datetime

import factory

from environments.tests.factories import ProjectFactory


class TaskFactory(factory.django.DjangoModelFactory):
    """Factory for islam_scheduling.Task."""

    class Meta:
        model = "islam_scheduling.Task"

    project = factory.SubFactory(ProjectFactory)
    name = factory.Sequence(lambda n: f"Task {n:03d}")
    start_date = factory.LazyFunction(lambda: datetime.date(2025, 1, 1))
    end_date = factory.LazyFunction(lambda: datetime.date(2025, 3, 31))
    status = "planned"
    activity_code = factory.Sequence(lambda n: f"A{n:04d}")
    stage = ""
    sub_stage = ""
    is_non_physical = False
    is_critical = False
