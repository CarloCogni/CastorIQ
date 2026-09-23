# castor/scheduling/tests/factories.py
"""Factory Boy factories for castor scheduling models."""

from __future__ import annotations

import datetime

import factory

from environments.tests.factories import ProjectFactory


class TaskFactory(factory.django.DjangoModelFactory):
    """Factory for castor_scheduling.Task."""

    class Meta:
        model = "castor_scheduling.Task"

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


class ResourceFactory(factory.django.DjangoModelFactory):
    """Factory for canonical DF-E1 Resource."""

    class Meta:
        model = "castor_scheduling.Resource"

    project = factory.SubFactory(ProjectFactory)
    name = factory.Sequence(lambda n: f"Resource {n:03d}")
    resource_code = factory.Sequence(lambda n: f"R{n:04d}")
    resource_type = "labor"
    status = "active"


class ResourceAssignmentFactory(factory.django.DjangoModelFactory):
    """Factory for canonical DF-E1 ResourceAssignment."""

    class Meta:
        model = "castor_scheduling.ResourceAssignment"

    project = factory.LazyAttribute(lambda o: o.task.project)
    task = factory.SubFactory(TaskFactory)
    resource = factory.SubFactory(
        ResourceFactory,
        project=factory.SelfAttribute("..task.project"),
    )
    is_pending = False
    status = "active"
