# castor/tests/test_parsers.py
"""Unit tests for schedule file parsers and the column mapper helpers.

All parser tests exercise synthetic minimal input — no live files required.
None of these tests hit the database.
"""

from __future__ import annotations

import io
from datetime import date

from django.test import TestCase

from .fixtures import excel_bytes, mspxml_bytes, p6xml_bytes, xer_bytes

# ---------------------------------------------------------------------------
# XER parser
# ---------------------------------------------------------------------------


class XerParserTests(TestCase):
    def _parse(self, **kw):
        from scheduling.services.xer_parser import parse_xer

        return parse_xer(io.BytesIO(xer_bytes(**kw)))

    def test_xer_parses_tasks(self):
        """Three TASK rows → three task dicts with required fields."""
        raw = xer_bytes(
            tasks=[
                {
                    "task_id": "1",
                    "task_code": "A1",
                    "task_name": "Task One",
                    "target_start_date": "2025-01-01 08:00",
                    "target_end_date": "2025-01-05 08:00",
                    "status_code": "TK_NotStart",
                    "act_start_date": "",
                    "act_end_date": "",
                },
                {
                    "task_id": "2",
                    "task_code": "A2",
                    "task_name": "Task Two",
                    "target_start_date": "2025-01-06 08:00",
                    "target_end_date": "2025-01-10 08:00",
                    "status_code": "TK_Active",
                    "act_start_date": "2025-01-06 08:00",
                    "act_end_date": "",
                },
                {
                    "task_id": "3",
                    "task_code": "A3",
                    "task_name": "Task Three",
                    "target_start_date": "2025-01-11 08:00",
                    "target_end_date": "2025-01-15 08:00",
                    "status_code": "TK_Complete",
                    "act_start_date": "2025-01-11 08:00",
                    "act_end_date": "2025-01-14 08:00",
                },
            ]
        )
        from scheduling.services.xer_parser import parse_xer

        tasks, deps = parse_xer(io.BytesIO(raw))
        self.assertEqual(len(tasks), 3)
        self.assertEqual(tasks[0]["name"], "Task One")
        self.assertEqual(tasks[0]["activity_code"], "A1")
        self.assertEqual(tasks[0]["source"], "xer")
        self.assertIsInstance(tasks[0]["start_date"], date)
        self.assertIsInstance(tasks[0]["end_date"], date)

    def test_xer_parses_dependencies(self):
        """TASKPRED rows → raw_dep dicts with correct keys."""
        raw = xer_bytes(
            tasks=[
                {
                    "task_id": "1",
                    "task_code": "A1",
                    "task_name": "Task One",
                    "target_start_date": "2025-01-01 08:00",
                    "target_end_date": "2025-01-05 08:00",
                    "status_code": "",
                    "act_start_date": "",
                    "act_end_date": "",
                },
                {
                    "task_id": "2",
                    "task_code": "A2",
                    "task_name": "Task Two",
                    "target_start_date": "2025-01-06 08:00",
                    "target_end_date": "2025-01-10 08:00",
                    "status_code": "",
                    "act_start_date": "",
                    "act_end_date": "",
                },
            ],
            deps=[
                {
                    "task_id": "2",
                    "pred_task_id": "1",
                    "pred_type": "PR_FS",
                    "lag_hr_cnt": "16",
                }
            ],
        )
        from scheduling.services.xer_parser import parse_xer

        tasks, deps = parse_xer(io.BytesIO(raw))
        self.assertEqual(len(deps), 1)
        d = deps[0]
        self.assertEqual(d["pred_xer_id"], "1")
        self.assertEqual(d["succ_xer_id"], "2")
        self.assertEqual(d["dep_type"], "FS")
        self.assertEqual(d["lag_days"], 2)  # 16 hr / 8 = 2 days

    def test_xer_preview_only_caps_tasks(self):
        """preview_only=True → at most 200 tasks, no deps returned."""
        many_tasks = [
            {
                "task_id": str(i),
                "task_code": f"T{i:04d}",
                "task_name": f"Task {i}",
                "target_start_date": "2025-01-01 08:00",
                "target_end_date": "2025-01-10 08:00",
                "status_code": "",
                "act_start_date": "",
                "act_end_date": "",
            }
            for i in range(1, 251)
        ]
        many_deps = [{"task_id": "2", "pred_task_id": "1", "pred_type": "PR_FS", "lag_hr_cnt": "0"}]
        from scheduling.services.xer_parser import parse_xer

        tasks, deps = parse_xer(io.BytesIO(xer_bytes(many_tasks, many_deps)), preview_only=True)
        self.assertLessEqual(len(tasks), 200)
        self.assertEqual(len(deps), 0)

    def test_xer_skips_unneeded_tables(self):
        """Tables outside NEEDED_TABLES are silently skipped."""
        raw = xer_bytes(
            extra_tables=[
                (
                    "SOMEOTHERTABLE",
                    ["field_a", "field_b"],
                    [["val1", "val2"]],
                )
            ]
        )
        from scheduling.services.xer_parser import parse_xer

        # Should not raise; the extra table rows should be ignored
        tasks, deps = parse_xer(io.BytesIO(raw))
        self.assertEqual(len(tasks), 1)  # only the default task from xer_bytes

    def test_xer_actual_dates(self):
        """act_start_date / act_end_date → actual_start / actual_end on task dict."""
        raw = xer_bytes(
            tasks=[
                {
                    "task_id": "1",
                    "task_code": "A1",
                    "task_name": "Done Task",
                    "target_start_date": "2025-01-01 08:00",
                    "target_end_date": "2025-01-10 08:00",
                    "status_code": "TK_Complete",
                    "act_start_date": "2025-01-02 08:00",
                    "act_end_date": "2025-01-09 08:00",
                }
            ]
        )
        from scheduling.services.xer_parser import parse_xer

        tasks, _ = parse_xer(io.BytesIO(raw))
        self.assertEqual(tasks[0]["actual_start"], date(2025, 1, 2))
        self.assertEqual(tasks[0]["actual_end"], date(2025, 1, 9))

    def test_xer_strips_star_placeholder(self):
        """act_start_date='*' (P6 not-yet-started sentinel) → actual_start=None."""
        raw = xer_bytes(
            tasks=[
                {
                    "task_id": "1",
                    "task_code": "A1",
                    "task_name": "Not Started",
                    "target_start_date": "2025-06-01 08:00",
                    "target_end_date": "2025-06-10 08:00",
                    "status_code": "TK_NotStart",
                    "act_start_date": "*",
                    "act_end_date": "*",
                }
            ]
        )
        from scheduling.services.xer_parser import parse_xer

        tasks, _ = parse_xer(io.BytesIO(raw))
        self.assertIsNone(tasks[0]["actual_start"])
        self.assertIsNone(tasks[0]["actual_end"])

    def test_xer_no_tasks_raises(self):
        """File with no TASK table → ValueError."""
        raw = b"%T\tPROJECT\n%F\tproj_id\n%R\t100\n%E\n"
        from scheduling.services.xer_parser import parse_xer

        with self.assertRaises(ValueError):
            parse_xer(io.BytesIO(raw))


# ---------------------------------------------------------------------------
# P6 XML parser
# ---------------------------------------------------------------------------


class P6XmlParserTests(TestCase):
    def _parse(self, **kw):
        from scheduling.services.msp_parser import parse_msp

        return parse_msp(io.BytesIO(p6xml_bytes(**kw)))

    def test_p6xml_detects_format(self):
        """APIBusinessObjects root → tasks have source='p6xml'."""
        tasks, _ = self._parse()
        self.assertTrue(all(t["source"] == "p6xml" for t in tasks))

    def test_p6xml_parses_activities(self):
        """Three activities → three task dicts with required fields."""
        acts = [
            {
                "ObjectId": str(i),
                "Id": f"ACT-{i:03d}",
                "Name": f"Activity {i}",
                "PlannedStartDate": "2025-01-01T08:00:00",
                "PlannedFinishDate": "2025-01-10T08:00:00",
                "Status": "Not Started",
                "PercentComplete": "0",
                "ActualStartDate": "",
                "ActualFinishDate": "",
            }
            for i in range(1, 4)
        ]
        tasks, _ = self._parse(activities=acts)
        self.assertEqual(len(tasks), 3)
        self.assertEqual(tasks[0]["activity_code"], "ACT-001")
        self.assertIsInstance(tasks[0]["start_date"], date)

    def test_p6xml_parses_relationships(self):
        """Relationship elements → dep list with correct keys."""
        acts = [
            {
                "ObjectId": "100",
                "Id": "A1",
                "Name": "Activity A",
                "PlannedStartDate": "2025-01-01T08:00:00",
                "PlannedFinishDate": "2025-01-05T08:00:00",
                "Status": "Not Started",
                "PercentComplete": "0",
            },
            {
                "ObjectId": "101",
                "Id": "A2",
                "Name": "Activity B",
                "PlannedStartDate": "2025-01-06T08:00:00",
                "PlannedFinishDate": "2025-01-10T08:00:00",
                "Status": "Not Started",
                "PercentComplete": "0",
            },
        ]
        rels = [{"pred": "100", "succ": "101", "type": "Finish to Start", "lag": "8"}]
        tasks, deps = self._parse(activities=acts, relationships=rels)
        self.assertEqual(len(deps), 1)
        d = deps[0]
        self.assertEqual(d["pred_p6_obj_id"], "100")
        self.assertEqual(d["succ_p6_obj_id"], "101")
        self.assertEqual(d["dep_type"], "FS")
        self.assertEqual(d["lag_days"], 1)  # 8 hr / 8 = 1 day

    def test_p6xml_preview_only(self):
        """preview_only=True → at most 200 tasks, no deps."""
        acts = [
            {
                "ObjectId": str(i),
                "Id": f"ACT-{i}",
                "Name": f"Activity {i}",
                "PlannedStartDate": "2025-01-01T08:00:00",
                "PlannedFinishDate": "2025-01-10T08:00:00",
                "Status": "Not Started",
                "PercentComplete": "0",
            }
            for i in range(1, 251)
        ]
        rels = [{"pred": "1", "succ": "2", "type": "Finish to Start", "lag": "0"}]
        from scheduling.services.msp_parser import parse_msp

        tasks, deps = parse_msp(
            io.BytesIO(p6xml_bytes(activities=acts, relationships=rels)), preview_only=True
        )
        self.assertLessEqual(len(tasks), 200)
        self.assertEqual(len(deps), 0)

    def test_p6xml_namespace_handling(self):
        """Oracle namespace on all elements → correctly parsed, not empty."""
        tasks, _ = self._parse()
        self.assertGreater(len(tasks), 0)
        for t in tasks:
            self.assertNotEqual(t["name"], "")
            self.assertIsNotNone(t["start_date"])

    def test_p6xml_percent_complete_fraction(self):
        """PercentComplete='0.6035' (P6 Professional 0-1 fraction) → active, not crash."""
        acts = [
            {
                "ObjectId": "1",
                "Id": "A1",
                "Name": "In-Progress Activity",
                "PlannedStartDate": "2025-01-01T08:00:00",
                "PlannedFinishDate": "2025-01-10T08:00:00",
                "Status": "In Progress",
                "PercentComplete": "0.6035",
                "ActualStartDate": "2025-01-01T08:00:00",
                "ActualFinishDate": "",
            }
        ]
        tasks, _ = self._parse(activities=acts)
        self.assertEqual(tasks[0]["status"], "active")

    def test_p6xml_percent_complete_full_integer(self):
        """PercentComplete='1' (completed) → status='complete'."""
        acts = [
            {
                "ObjectId": "1",
                "Id": "A1",
                "Name": "Done",
                "PlannedStartDate": "2025-01-01T08:00:00",
                "PlannedFinishDate": "2025-01-10T08:00:00",
                "Status": "Completed",
                "PercentComplete": "1",
                "ActualStartDate": "2025-01-01T08:00:00",
                "ActualFinishDate": "2025-01-09T08:00:00",
            }
        ]
        tasks, _ = self._parse(activities=acts)
        self.assertEqual(tasks[0]["status"], "complete")

    def test_p6xml_actual_dates(self):
        """ActualStartDate / ActualFinishDate → actual_start / actual_end."""
        tasks, _ = self._parse()  # default fixture has actual dates
        self.assertEqual(tasks[0]["actual_start"], date(2025, 1, 1))
        self.assertEqual(tasks[0]["actual_end"], date(2025, 1, 9))

    def test_p6xml_na_actual_dates(self):
        """ActualStartDate='NA' → actual_start=None."""
        acts = [
            {
                "ObjectId": "1",
                "Id": "A1",
                "Name": "Not Started",
                "PlannedStartDate": "2025-06-01T08:00:00",
                "PlannedFinishDate": "2025-06-10T08:00:00",
                "Status": "Not Started",
                "PercentComplete": "0",
                "ActualStartDate": "NA",
                "ActualFinishDate": "N/A",
            }
        ]
        tasks, _ = self._parse(activities=acts)
        self.assertIsNone(tasks[0]["actual_start"])
        self.assertIsNone(tasks[0]["actual_end"])

    def test_p6xml_ss_dep_type(self):
        """Relationship Type='Start to Start' → dep_type='SS'."""
        acts = [
            {
                "ObjectId": "1",
                "Id": "A1",
                "Name": "Task A",
                "PlannedStartDate": "2025-01-01T08:00:00",
                "PlannedFinishDate": "2025-01-05T08:00:00",
                "Status": "Not Started",
                "PercentComplete": "0",
            },
            {
                "ObjectId": "2",
                "Id": "A2",
                "Name": "Task B",
                "PlannedStartDate": "2025-01-01T08:00:00",
                "PlannedFinishDate": "2025-01-10T08:00:00",
                "Status": "Not Started",
                "PercentComplete": "0",
            },
        ]
        rels = [{"pred": "1", "succ": "2", "type": "Start to Start", "lag": "0"}]
        _, deps = self._parse(activities=acts, relationships=rels)
        self.assertEqual(deps[0]["dep_type"], "SS")


# ---------------------------------------------------------------------------
# MSP XML parser
# ---------------------------------------------------------------------------


class MspXmlParserTests(TestCase):
    def _parse(self, **kw):
        from scheduling.services.msp_parser import parse_msp

        return parse_msp(io.BytesIO(mspxml_bytes(**kw)))

    def test_msp_detects_format(self):
        """Project/Tasks root → tasks have source='msp'."""
        tasks, _ = self._parse()
        self.assertTrue(all(t["source"] == "msp" for t in tasks))

    def test_msp_parses_tasks(self):
        """Three tasks → three task dicts with required fields."""
        task_list = [
            {
                "UID": str(i),
                "Name": f"MSP Task {i}",
                "Start": "2025-01-01T08:00:00",
                "Finish": "2025-01-10T08:00:00",
                "Status": "0",
                "PercentComplete": "0",
                "Milestone": "0",
                "predecessors": [],
            }
            for i in range(1, 4)
        ]
        tasks, _ = self._parse(tasks=task_list)
        self.assertEqual(len(tasks), 3)
        self.assertIsInstance(tasks[0]["start_date"], date)
        self.assertEqual(tasks[0]["source"], "msp")

    def test_msp_predecessor_links(self):
        """PredecessorLink elements → dep list."""
        task_list = [
            {
                "UID": "1",
                "Name": "Predecessor",
                "Start": "2025-01-01T08:00:00",
                "Finish": "2025-01-05T08:00:00",
                "Status": "2",
                "PercentComplete": "100",
                "Milestone": "0",
                "predecessors": [],
            },
            {
                "UID": "2",
                "Name": "Successor",
                "Start": "2025-01-06T08:00:00",
                "Finish": "2025-01-10T08:00:00",
                "Status": "0",
                "PercentComplete": "0",
                "Milestone": "0",
                "predecessors": [{"uid": "1", "type": "1"}],
            },
        ]
        tasks, deps = self._parse(tasks=task_list)
        self.assertEqual(len(deps), 1)
        self.assertEqual(deps[0]["pred_uid"], "1")
        self.assertEqual(deps[0]["succ_uid"], "2")
        self.assertEqual(deps[0]["dep_type"], "FS")

    def test_msp_preview_only(self):
        """preview_only=True → at most 200 tasks, no deps."""
        task_list = [
            {
                "UID": str(i),
                "Name": f"Task {i}",
                "Start": "2025-01-01T08:00:00",
                "Finish": "2025-01-10T08:00:00",
                "Status": "0",
                "PercentComplete": "0",
                "Milestone": "0",
                "predecessors": [{"uid": str(i - 1), "type": "1"}] if i > 1 else [],
            }
            for i in range(1, 251)
        ]
        from scheduling.services.msp_parser import parse_msp

        tasks, deps = parse_msp(io.BytesIO(mspxml_bytes(tasks=task_list)), preview_only=True)
        self.assertLessEqual(len(tasks), 200)
        self.assertEqual(len(deps), 0)

    def test_msp_actual_dates(self):
        """ActualStart / ActualFinish → actual_start / actual_end on task dict."""
        task_list = [
            {
                "UID": "1",
                "Name": "Completed Task",
                "Start": "2025-01-01T08:00:00",
                "Finish": "2025-01-10T08:00:00",
                "Status": "2",
                "PercentComplete": "100",
                "ActualStart": "2025-01-02T08:00:00",
                "ActualFinish": "2025-01-09T08:00:00",
                "Milestone": "0",
                "predecessors": [],
            }
        ]
        tasks, _ = self._parse(tasks=task_list)
        self.assertEqual(tasks[0]["actual_start"], date(2025, 1, 2))
        self.assertEqual(tasks[0]["actual_end"], date(2025, 1, 9))

    def test_msp_strips_na_placeholder(self):
        """ActualStart='NA' → actual_start=None."""
        task_list = [
            {
                "UID": "1",
                "Name": "Future Task",
                "Start": "2025-06-01T08:00:00",
                "Finish": "2025-06-10T08:00:00",
                "Status": "0",
                "PercentComplete": "0",
                "ActualStart": "NA",
                "ActualFinish": "N/A",
                "Milestone": "0",
                "predecessors": [],
            }
        ]
        tasks, _ = self._parse(tasks=task_list)
        self.assertIsNone(tasks[0]["actual_start"])
        self.assertIsNone(tasks[0]["actual_end"])

    def test_msp_summary_rows_included(self):
        """Summary=1 tasks are NOT filtered — WBS phase rows are valid tasks."""
        task_list = [
            {
                "UID": "1",
                "Name": "Phase 1: Substructure",
                "Start": "2025-01-01T08:00:00",
                "Finish": "2025-03-31T08:00:00",
                "Status": "1",
                "PercentComplete": "50",
                "Summary": "1",
                "Milestone": "0",
                "predecessors": [],
            }
        ]
        tasks, _ = self._parse(tasks=task_list)
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["name"], "Phase 1: Substructure")

    def test_msp_milestone_filtered(self):
        """Milestone=1 tasks are excluded from output."""
        task_list = [
            {
                "UID": "1",
                "Name": "Real Task",
                "Start": "2025-01-01T08:00:00",
                "Finish": "2025-01-10T08:00:00",
                "Status": "0",
                "PercentComplete": "0",
                "Milestone": "0",
                "predecessors": [],
            },
            {
                "UID": "2",
                "Name": "Milestone Event",
                "Start": "2025-01-10T08:00:00",
                "Finish": "2025-01-10T08:00:00",
                "Status": "0",
                "PercentComplete": "0",
                "Milestone": "1",
                "predecessors": [],
            },
        ]
        tasks, _ = self._parse(tasks=task_list)
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["name"], "Real Task")

    def test_msp_uid_0_filtered(self):
        """UID=0 (invisible project root) is excluded."""
        task_list = [
            {
                "UID": "0",
                "Name": "Project Root",
                "Start": "2025-01-01T08:00:00",
                "Finish": "2025-12-31T08:00:00",
                "Status": "0",
                "PercentComplete": "0",
                "Milestone": "0",
                "predecessors": [],
            },
            {
                "UID": "1",
                "Name": "Real Task",
                "Start": "2025-01-01T08:00:00",
                "Finish": "2025-01-10T08:00:00",
                "Status": "0",
                "PercentComplete": "0",
                "Milestone": "0",
                "predecessors": [],
            },
        ]
        tasks, _ = self._parse(tasks=task_list)
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["name"], "Real Task")

    def test_msp_percent_complete_decimal_no_crash(self):
        """PercentComplete='55.50' (decimal string) → int conversion doesn't crash."""
        task_list = [
            {
                "UID": "1",
                "Name": "In Progress",
                "Start": "2025-01-01T08:00:00",
                "Finish": "2025-01-10T08:00:00",
                "Status": "1",
                "PercentComplete": "55.50",
                "Milestone": "0",
                "predecessors": [],
            }
        ]
        tasks, _ = self._parse(tasks=task_list)
        self.assertEqual(tasks[0]["status"], "active")

    def test_msp_ff_dep_type(self):
        """PredecessorLink Type=0 → dep_type='FF'."""
        task_list = [
            {
                "UID": "1",
                "Name": "Task A",
                "Start": "2025-01-01T08:00:00",
                "Finish": "2025-01-05T08:00:00",
                "Status": "0",
                "PercentComplete": "0",
                "Milestone": "0",
                "predecessors": [],
            },
            {
                "UID": "2",
                "Name": "Task B",
                "Start": "2025-01-01T08:00:00",
                "Finish": "2025-01-10T08:00:00",
                "Status": "0",
                "PercentComplete": "0",
                "Milestone": "0",
                "predecessors": [{"uid": "1", "type": "0"}],  # 0 = FF
            },
        ]
        _, deps = self._parse(tasks=task_list)
        self.assertEqual(deps[0]["dep_type"], "FF")


# ---------------------------------------------------------------------------
# Excel parser
# ---------------------------------------------------------------------------


class ExcelParserTests(TestCase):
    def test_excel_parses_headers(self):
        """Sheet with standard headers → task dicts returned."""
        raw = excel_bytes(
            headers=["Task Name", "Start Date", "End Date", "Status", "Activity Code"],
            rows=[
                ["Foundation Works", date(2025, 1, 1), date(2025, 1, 10), "planned", "A001"],
                ["Structural Frame", date(2025, 1, 11), date(2025, 1, 20), "active", "A002"],
            ],
        )
        from scheduling.services.excel_parser import parse_excel

        tasks = parse_excel(io.BytesIO(raw))
        self.assertEqual(len(tasks), 2)
        self.assertEqual(tasks[0]["name"], "Foundation Works")
        self.assertEqual(tasks[0]["start_date"], date(2025, 1, 1))
        self.assertEqual(tasks[0]["activity_code"], "A001")
        self.assertEqual(tasks[0]["source"], "excel")

    def test_excel_preview_only(self):
        """preview_only=True with 300 data rows → at most 200 tasks returned."""
        rows = [
            [f"Task {i}", date(2025, 1, 1), date(2025, 1, 10), "planned", f"T{i:04d}"]
            for i in range(1, 301)
        ]
        raw = excel_bytes(
            headers=["Task Name", "Start Date", "End Date", "Status", "Activity Code"],
            rows=rows,
        )
        from scheduling.services.excel_parser import parse_excel

        tasks = parse_excel(io.BytesIO(raw), preview_only=True)
        self.assertLessEqual(len(tasks), 200)

    def test_excel_lazy_streaming_full_mode(self):
        """preview_only=False → all rows returned."""
        rows = [
            [f"Task {i}", date(2025, 1, 1), date(2025, 1, 10), "planned", f"T{i:04d}"]
            for i in range(1, 51)
        ]
        raw = excel_bytes(
            headers=["Task Name", "Start Date", "End Date", "Status", "Activity Code"],
            rows=rows,
        )
        from scheduling.services.excel_parser import parse_excel

        tasks = parse_excel(io.BytesIO(raw), preview_only=False)
        self.assertEqual(len(tasks), 50)

    def test_excel_no_header_raises(self):
        """Sheet with no recognisable header → ValueError."""
        raw = excel_bytes(
            headers=["Col A", "Col B", "Col C"],
            rows=[["data1", "data2", "data3"]],
        )
        from scheduling.services.excel_parser import parse_excel

        with self.assertRaises(ValueError):
            parse_excel(io.BytesIO(raw))


# ---------------------------------------------------------------------------
# Column mapper helpers
# ---------------------------------------------------------------------------


class ColumnMapperTests(TestCase):
    def test_parse_predecessor_string_multi(self):
        """'A1010FS+2d,A1020SS' → two dep dicts with correct fields."""
        from scheduling.services.column_mapper import parse_predecessor_string

        result = parse_predecessor_string("A1010FS+2d,A1020SS")
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["activity_code"], "A1010")
        self.assertEqual(result[0]["dep_type"], "FS")
        self.assertEqual(result[0]["lag_days"], 2)
        self.assertEqual(result[1]["activity_code"], "A1020")
        self.assertEqual(result[1]["dep_type"], "SS")
        self.assertEqual(result[1]["lag_days"], 0)

    def test_parse_predecessor_string_default_fs(self):
        """Activity code with no dep_type suffix → defaults to FS, lag=0."""
        from scheduling.services.column_mapper import parse_predecessor_string

        result = parse_predecessor_string("A1010")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["dep_type"], "FS")
        self.assertEqual(result[0]["lag_days"], 0)

    def test_parse_predecessor_string_empty(self):
        """Empty string → empty list, no crash."""
        from scheduling.services.column_mapper import parse_predecessor_string

        result = parse_predecessor_string("")
        self.assertEqual(result, [])

    def test_cost_parsing_currency(self):
        """'$1,200.50' → cleaned decimal string '1200.50'."""
        from scheduling.services.column_mapper import _parse_cost

        self.assertEqual(_parse_cost("$1,200.50"), "1200.50")

    def test_cost_parsing_plain(self):
        """'50000' → '50000'."""
        from scheduling.services.column_mapper import _parse_cost

        self.assertEqual(_parse_cost("50000"), "50000")

    def test_cost_parsing_empty(self):
        """Empty string → None."""
        from scheduling.services.column_mapper import _parse_cost

        self.assertIsNone(_parse_cost(""))

    def test_cost_parsing_non_numeric(self):
        """Non-numeric string → None."""
        from scheduling.services.column_mapper import _parse_cost

        self.assertIsNone(_parse_cost("TBD"))
