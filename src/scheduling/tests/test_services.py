# castor/tests/test_services.py
"""Tests for castor scheduling services: CPM, EVM, stage detection, layer-0."""

from __future__ import annotations

from datetime import date

from django.test import TestCase

from .fixtures import make_dependency, make_project, make_task

# ---------------------------------------------------------------------------
# Critical Path Method
# ---------------------------------------------------------------------------


class CriticalPathTests(TestCase):
    """compute_critical_path() — Kahn's algorithm + forward/backward CPM passes."""

    def setUp(self):
        from datetime import date

        from scheduling.models import ScheduleSource

        self.project = make_project()
        # Pin the project data_date to before all test task dates (2025-xx-xx).
        # Without this, get_project_data_date() falls back to date.today(), and
        # the CPM's "floor at data_date" pushes future-relative dates into the
        # present, breaking date assertions.
        ScheduleSource.objects.create(
            project=self.project,
            filename="test.xer",
            source_format="xer",
            data_date=date(2024, 12, 1),
        )

    def _cpm(self):
        from scheduling.services.critical_path import compute_critical_path

        return compute_critical_path(str(self.project.pk))

    def test_no_tasks_returns_empty(self):
        result = self._cpm()
        self.assertFalse(result["critical_task_ids"])
        self.assertEqual(result["project_duration"], 0)
        self.assertEqual(result["task_data"], {})

    def test_single_task_is_critical(self):
        t = make_task(self.project, start_date=date(2025, 1, 1), end_date=date(2025, 1, 5))
        result = self._cpm()
        self.assertIn(str(t.pk), result["critical_task_ids"])
        self.assertEqual(result["project_duration"], 5)  # 1–5 Jan inclusive

    def test_linear_chain_all_tasks_critical(self):
        """A → B (FS, no lag) — both tasks are on the critical path."""
        a = make_task(self.project, start_date=date(2025, 1, 1), end_date=date(2025, 1, 5))
        b = make_task(self.project, start_date=date(2025, 1, 6), end_date=date(2025, 1, 10))
        make_dependency(a, b, "FS")
        result = self._cpm()
        self.assertIn(str(a.pk), result["critical_task_ids"])
        self.assertIn(str(b.pk), result["critical_task_ids"])

    def test_parallel_paths_longer_path_is_critical(self):
        """
        A (1–5 Jan, 5d) → C
        B (1–3 Jan, 3d) → C
        A→C is the critical path; B has positive float.
        """
        a = make_task(self.project, start_date=date(2025, 1, 1), end_date=date(2025, 1, 5))
        b = make_task(self.project, start_date=date(2025, 1, 1), end_date=date(2025, 1, 3))
        c = make_task(self.project, start_date=date(2025, 1, 6), end_date=date(2025, 1, 10))
        make_dependency(a, c, "FS")
        make_dependency(b, c, "FS")
        result = self._cpm()
        self.assertIn(str(a.pk), result["critical_task_ids"])
        self.assertIn(str(c.pk), result["critical_task_ids"])
        b_data = result["task_data"][str(b.pk)]
        self.assertGreater(b_data["total_float"], 0)
        self.assertFalse(b_data["is_critical"])

    def test_fs_lag_shifts_early_start(self):
        """A (1–5 Jan) → B with 2-day FS lag: B early_start = 8 Jan."""
        a = make_task(self.project, start_date=date(2025, 1, 1), end_date=date(2025, 1, 5))
        b = make_task(self.project, start_date=date(2025, 1, 8), end_date=date(2025, 1, 12))
        make_dependency(a, b, "FS", lag=2)
        result = self._cpm()
        b_data = result["task_data"][str(b.pk)]
        self.assertEqual(b_data["early_start"], "2025-01-08")

    def test_ss_dependency(self):
        """A SS B: B can start at the same time as A."""
        a = make_task(self.project, start_date=date(2025, 1, 1), end_date=date(2025, 1, 10))
        b = make_task(self.project, start_date=date(2025, 1, 1), end_date=date(2025, 1, 5))
        make_dependency(a, b, "SS")
        result = self._cpm()
        b_data = result["task_data"][str(b.pk)]
        self.assertEqual(b_data["early_start"], "2025-01-01")

    def test_cycle_does_not_raise(self):
        """Cyclic dependency falls back to date-order — no exception raised."""
        a = make_task(self.project, start_date=date(2025, 1, 1), end_date=date(2025, 1, 5))
        b = make_task(self.project, start_date=date(2025, 1, 6), end_date=date(2025, 1, 10))
        make_dependency(a, b, "FS")
        make_dependency(b, a, "FS")  # creates cycle
        result = self._cpm()
        self.assertIn("critical_task_ids", result)

    def test_non_physical_tasks_excluded(self):
        """is_non_physical=True tasks must not appear in CPM results."""
        phys = make_task(self.project, start_date=date(2025, 1, 1), end_date=date(2025, 1, 5))
        non_phys = make_task(
            self.project,
            name="Submittal Review",
            start_date=date(2025, 1, 1),
            end_date=date(2025, 1, 15),
            is_non_physical=True,
        )
        result = self._cpm()
        self.assertIn(str(phys.pk), result["task_data"])
        self.assertNotIn(str(non_phys.pk), result["task_data"])

    def test_results_persisted_to_db(self):
        """CPM writes early_start, late_finish, is_critical back to Task rows."""

        a = make_task(self.project, start_date=date(2025, 1, 1), end_date=date(2025, 1, 5))
        b = make_task(self.project, start_date=date(2025, 1, 6), end_date=date(2025, 1, 10))
        make_dependency(a, b, "FS")
        self._cpm()
        a.refresh_from_db()
        b.refresh_from_db()
        self.assertIsNotNone(a.early_start)
        self.assertIsNotNone(b.late_finish)
        self.assertTrue(a.is_critical)
        self.assertTrue(b.is_critical)


# ---------------------------------------------------------------------------
# Earned Value Management
# ---------------------------------------------------------------------------


class EVMServiceTests(TestCase):
    """compute_evm() — PV/EV/AC/SPI/CPI derivation and S-curve series."""

    def setUp(self):
        self.project = make_project()

    def _evm(self, as_of=None):
        from scheduling.services.evm import compute_evm

        return compute_evm(str(self.project.pk), as_of_date=as_of)

    def test_no_tasks_returns_no_data(self):
        result = self._evm()
        self.assertFalse(result["has_data"])

    def test_duration_fallback_when_no_cost(self):
        make_task(self.project, start_date=date(2025, 1, 1), end_date=date(2025, 1, 10))
        result = self._evm(as_of=date(2025, 1, 20))
        self.assertTrue(result["has_data"])
        self.assertFalse(result["use_cost"])
        self.assertEqual(result["cost_basis"], "task durations")

    def test_cost_basis_when_schedule_cost_present(self):
        make_task(self.project, start_date=date(2025, 1, 1), end_date=date(2025, 1, 10), cost=100)
        result = self._evm(as_of=date(2025, 1, 20))
        self.assertTrue(result["use_cost"])
        self.assertEqual(result["cost_basis"], "schedule costs")

    def test_complete_task_fully_earned(self):
        make_task(
            self.project,
            start_date=date(2025, 1, 1),
            end_date=date(2025, 1, 10),
            cost=100,
            status="complete",
        )
        result = self._evm(as_of=date(2025, 2, 1))
        self.assertAlmostEqual(result["ev"], 100.0, places=1)

    def test_future_planned_task_has_zero_ev(self):
        make_task(
            self.project,
            start_date=date(2030, 1, 1),
            end_date=date(2030, 1, 31),
            cost=200,
            status="planned",
        )
        result = self._evm(as_of=date(2025, 1, 1))
        self.assertAlmostEqual(result["ev"], 0.0, places=1)

    def test_behind_schedule_spi_below_one(self):
        """EV < PV → SPI < 1."""
        # Task A complete: full EV, partial PV
        make_task(
            self.project,
            name="Task A",
            start_date=date(2025, 1, 1),
            end_date=date(2025, 1, 10),
            cost=80,
            status="complete",
        )
        # Task B planned and overdue: PV = full, EV = 0
        make_task(
            self.project,
            name="Task B",
            start_date=date(2025, 1, 1),
            end_date=date(2025, 1, 10),
            cost=20,
            status="planned",
        )
        result = self._evm(as_of=date(2025, 1, 15))
        self.assertLess(result["spi"], 1.0)

    def test_ac_is_none_without_resource_assignments(self):
        # Synthetic AC (EV × 1.05) was removed; without P6 ResourceAssignments
        # the service returns ac=None and ac_available=False.
        make_task(
            self.project,
            start_date=date(2025, 1, 1),
            end_date=date(2025, 1, 10),
            cost=100,
            status="complete",
        )
        result = self._evm(as_of=date(2025, 2, 1))
        self.assertIsNone(result["ac"])
        self.assertFalse(result["ac_available"])

    def test_series_has_required_structure(self):
        # series["ac"] is only populated when P6 ResourceAssignments are present;
        # without them ac_available=False and the ac series is absent.
        make_task(
            self.project,
            start_date=date(2025, 1, 1),
            end_date=date(2025, 3, 31),
            cost=100,
        )
        result = self._evm(as_of=date(2025, 2, 1))
        series = result["series"]
        for key in ("pv", "ev"):
            self.assertIn(key, series)
        self.assertGreater(len(series["pv"]), 0)
        point = series["pv"][0]
        self.assertIn("date", point)
        self.assertIn("pct", point)

    def test_non_physical_excluded_from_evm(self):
        """is_non_physical tasks must not affect EVM."""
        make_task(
            self.project,
            name="Meeting",
            start_date=date(2025, 1, 1),
            end_date=date(2025, 1, 5),
            cost=999,
            is_non_physical=True,
        )
        result = self._evm(as_of=date(2025, 2, 1))
        self.assertFalse(result["has_data"])


# ---------------------------------------------------------------------------
# Stage detection — pure functions (no DB required)
# ---------------------------------------------------------------------------


class StageDetectionTests(TestCase):
    """detect_task_stage / detect_task_sub_stage — keyword string matching."""

    def test_structure_stage_from_column(self):
        from scheduling.services.autolink import detect_task_stage

        self.assertEqual(detect_task_stage("Concrete Pour Column B5"), "structure")

    def test_structure_stage_from_slab(self):
        from scheduling.services.autolink import detect_task_stage

        self.assertEqual(detect_task_stage("Ground Floor Slab"), "structure")

    def test_mep_stage_from_hvac_keyword(self):
        from scheduling.services.autolink import detect_task_stage

        self.assertEqual(detect_task_stage("Install HVAC System Level 3"), "mep")

    def test_finishes_stage_from_plaster(self):
        from scheduling.services.autolink import detect_task_stage

        self.assertEqual(detect_task_stage("Internal Plaster Work"), "finishes")

    def test_external_stage_from_paving(self):
        from scheduling.services.autolink import detect_task_stage

        self.assertEqual(detect_task_stage("Car Park Paving"), "external")

    def test_no_match_returns_empty_string(self):
        from scheduling.services.autolink import detect_task_stage

        self.assertEqual(detect_task_stage("XYZ Unrecognised Activity 9999"), "")

    def test_stage_detection_is_case_insensitive(self):
        from scheduling.services.autolink import detect_task_stage

        self.assertEqual(detect_task_stage("CONCRETE POUR LEVEL 1"), "structure")

    def test_sub_stage_concrete(self):
        from scheduling.services.autolink import detect_task_sub_stage

        self.assertEqual(detect_task_sub_stage("Concrete Pour Level 3"), "concrete")

    def test_sub_stage_rebar(self):
        from scheduling.services.autolink import detect_task_sub_stage

        self.assertEqual(detect_task_sub_stage("Rebar Fixing Block B"), "rebar")

    def test_sub_stage_electrical(self):
        from scheduling.services.autolink import detect_task_sub_stage

        self.assertEqual(detect_task_sub_stage("Electrical Wiring 2nd Floor"), "electrical")

    def test_sub_stage_excavation(self):
        from scheduling.services.autolink import detect_task_sub_stage

        self.assertEqual(detect_task_sub_stage("Bulk Excavation Zone A"), "excavation")

    def test_sub_stage_no_match_returns_empty_string(self):
        from scheduling.services.autolink import detect_task_sub_stage

        self.assertEqual(detect_task_sub_stage("Admin Meeting"), "")


# ---------------------------------------------------------------------------
# autodetect_stages
# ---------------------------------------------------------------------------


class AutodetectStagesTests(TestCase):
    """autodetect_stages() — bulk stage/sub_stage assignment."""

    def setUp(self):
        self.project = make_project()

    def test_sets_stage_from_task_name(self):
        from scheduling.models import Task
        from scheduling.services.autolink import autodetect_stages

        task = make_task(self.project, name="Electrical Conduit Installation")
        updated = autodetect_stages(list(Task.objects.filter(project=self.project)))
        task.refresh_from_db()
        self.assertEqual(task.stage, "mep")
        self.assertEqual(updated, 1)

    def test_sub_stage_sets_parent_stage(self):
        from scheduling.models import Task
        from scheduling.services.autolink import autodetect_stages

        task = make_task(self.project, name="Rebar Fixing Level 4")
        autodetect_stages(list(Task.objects.filter(project=self.project)))
        task.refresh_from_db()
        self.assertEqual(task.sub_stage, "rebar")
        self.assertEqual(task.stage, "structure")

    def test_preserves_manually_set_stage(self):
        from scheduling.models import Task
        from scheduling.services.autolink import autodetect_stages

        task = make_task(self.project, name="Electrical Work", stage="envelope")
        updated = autodetect_stages(list(Task.objects.filter(project=self.project)))
        task.refresh_from_db()
        self.assertEqual(task.stage, "envelope")  # must not be overwritten
        self.assertEqual(updated, 0)

    def test_unknown_name_not_updated(self):
        from scheduling.models import Task
        from scheduling.services.autolink import autodetect_stages

        task = make_task(self.project, name="ZZZ Unknown Activity 9999")
        autodetect_stages(list(Task.objects.filter(project=self.project)))
        task.refresh_from_db()
        self.assertEqual(task.stage, "")


# ---------------------------------------------------------------------------
# Layer 0 — non-physical classification
# ---------------------------------------------------------------------------


class LayerZeroTests(TestCase):
    """_is_non_physical_auto / _run_layer0 — keyword-based pre-filter."""

    def setUp(self):
        self.project = make_project()

    def test_non_physical_keyword_in_name(self):
        from scheduling.services.autolink import _is_non_physical_auto

        task = make_task(self.project, name="Submittal Review Package A")
        self.assertTrue(_is_non_physical_auto(task))

    def test_physical_task_not_flagged(self):
        from scheduling.services.autolink import _is_non_physical_auto

        task = make_task(self.project, name="Concrete Pour Column B5")
        self.assertFalse(_is_non_physical_auto(task))

    def test_milestone_activity_type_flagged(self):
        from scheduling.services.autolink import _is_non_physical_auto

        task = make_task(self.project, name="Roof Completion", activity_type="Milestone")
        self.assertTrue(_is_non_physical_auto(task))

    def test_wbs_summary_type_flagged(self):
        from scheduling.services.autolink import _is_non_physical_auto

        task = make_task(self.project, name="Phase 1 Summary", activity_type="WBS Summary")
        self.assertTrue(_is_non_physical_auto(task))

    def test_locked_physical_not_reclassified(self):
        """Task locked as physical must stay physical even if name has a keyword."""
        from scheduling.services.autolink import _run_layer0

        task = make_task(
            self.project,
            name="Submittal Review",
            is_non_physical=False,
            non_physical_locked=True,
        )
        physical, non_physical = _run_layer0([task])
        task.refresh_from_db()
        self.assertIn(task, physical)
        self.assertFalse(task.is_non_physical)

    def test_locked_non_physical_not_reclassified(self):
        """Task locked as non-physical must stay non-physical even if name is physical."""
        from scheduling.services.autolink import _run_layer0

        task = make_task(
            self.project,
            name="Concrete Pour Column B5",
            is_non_physical=True,
            non_physical_locked=True,
        )
        physical, non_physical = _run_layer0([task])
        task.refresh_from_db()
        self.assertIn(task, non_physical)
        self.assertTrue(task.is_non_physical)

    def test_run_layer0_updates_db(self):
        """_run_layer0 must set is_non_physical=True on matching unlocked tasks."""
        from scheduling.services.autolink import _run_layer0

        task = make_task(self.project, name="Procurement Meeting", is_non_physical=False)
        _run_layer0([task])
        task.refresh_from_db()
        self.assertTrue(task.is_non_physical)
