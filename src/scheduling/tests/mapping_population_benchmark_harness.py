# scheduling/tests/mapping_population_benchmark_harness.py
"""DF-D2 mapping resolution performance harness — run via pytest or __main__."""

from __future__ import annotations

import time

from environments.tests.factories import ProjectFactory, UserFactory
from scheduling.models import AnalyticalMappingAssignment
from scheduling.services.governed_mapping.assignment import AnalyticalMappingAssignmentService
from scheduling.services.governed_mapping.coverage import MappingCoverageService
from scheduling.services.governed_mapping.dimension import AnalyticalDimensionService
from scheduling.services.governed_mapping.mapping_set import AnalyticalMappingSetService
from scheduling.services.governed_mapping.population import GovernedMappingPopulationService
from scheduling.services.governed_mapping.resolver import EffectiveMappingResolver
from scheduling.services.governed_mapping.value import AnalyticalDimensionValueService
from scheduling.tests.factories import TaskFactory


def _setup(project, n: int, user):
    from scheduling.models import AnalyticalDimension

    dim = AnalyticalDimensionService.create_draft(
        project=project,
        dimension_key="trade",
        name="Trade",
        dimension_type=AnalyticalDimension.DimensionType.TRADE,
        actor=user,
    )
    val = AnalyticalDimensionValueService(dim).create_value(name="Electrical", code="electrical")
    AnalyticalDimensionService.activate(dim, actor=user)
    mset = AnalyticalMappingSetService.create_draft(dimension=dim, name="Bench", actor=user)
    tasks = []
    for i in range(n):
        t = TaskFactory(project=project, sub_stage="electrical" if i % 2 == 0 else "concrete")
        tasks.append(t)
        if i % 3 == 0:
            AnalyticalMappingAssignmentService.assign_manually(
                mapping_set=mset,
                dimension_value=val,
                target_type=AnalyticalMappingAssignment.TargetType.TASK,
                task=t,
                auto_approve=True,
            )
    AnalyticalMappingSetService.activate(mset, actor=user)
    return dim, mset, tasks


def run_benchmark(sizes: tuple[int, ...] = (1000, 5000, 10000)) -> dict:
    """Return timing dict excluding fixture setup."""
    results: dict = {}
    for n in sizes:
        project = ProjectFactory()
        user = UserFactory()
        dim, _mset, tasks = _setup(project, n, user)
        ids = [t.pk for t in tasks]
        resolver = EffectiveMappingResolver(project)

        t0 = time.perf_counter()
        resolver.resolve_many_tasks(ids, dim)
        resolve_s = time.perf_counter() - t0

        t0 = time.perf_counter()
        MappingCoverageService(project).breakdown(dimension=dim)
        coverage_s = time.perf_counter() - t0

        t0 = time.perf_counter()
        GovernedMappingPopulationService(project).run_adoption(
            source="sub_stage_trade",
            dimension_key="trade",
            dry_run=True,
        )
        dry_run_s = time.perf_counter() - t0

        results[str(n)] = {
            "resolve_seconds": round(resolve_s, 3),
            "coverage_seconds": round(coverage_s, 3),
            "dry_run_seconds": round(dry_run_s, 3),
        }
    return results


if __name__ == "__main__":
    import django

    django.setup()
    import json

    print(json.dumps(run_benchmark((1000, 5000, 10000)), indent=2))
