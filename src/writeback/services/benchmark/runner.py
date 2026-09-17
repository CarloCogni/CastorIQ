# writeback/services/benchmark/runner.py
"""Run corpus cases through the V3 pipeline and score the outcome (spec B-1, B-2).

One case is one real request: ground → generate → run → verify on a scratch
copy of the project's IFC file. The runner scores what came back:

* **targets match** — the GlobalIds ``select`` returned equal the set the
  corpus expectation resolves to through the index (type, container, name,
  property filter). This is the column that decides the bake-off.
* **diff match** — every ``diff:`` line of the corpus is satisfied by the
  aggregated rows of the measured diff.
* **integrity** — nothing changed outside the selection and no geometry
  moved, measured **independently** of the pipeline's own gate: the scratch
  copy the child wrote is re-read from disk and diffed against the original
  with :meth:`IfcDiff.unexpected`. The gate already checked the in-memory
  diff, so this column catches a defect between that diff and the bytes on
  disk, or a drift between the two implementations of the rule.
* **reject / no-change** — cases that must be declined, or already hold.

A change case the model refused, or failed three times on, scores **false**
on targets and diff match rather than dropping out of those denominators. A
model that could not be reached (timeout, transport) is a harness error,
excluded from every score and listed on its own.

No proposal is persisted and the original file is never touched: the scratch
copy the pipeline kept is deleted after scoring.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path

from django.db.models import Q

from ifc_processor.models import IFCEntity
from ifc_processor.schema_data.lookup import expand_type
from ifc_processor.services.ifc_diff import diff_files
from ifc_processor.services.property_access import get_prop

from ..emitters import CapturingEmitter
from ..errors import ModelUnavailableError, ModificationError, NoChangeError
from ..pipeline import ModifyPipeline, PipelineOutcome
from ..verifier import DiffRow
from .corpus import BenchmarkCase, DiffExpectation, TargetExpectation

logger = logging.getLogger(__name__)


@dataclass
class CaseResult:
    """What happened when one case ran once."""

    case_id: str
    section_number: str
    prompt: str
    expectation: str
    kind: str  # change | reject | no_change | advisory
    advisory: bool

    outcome: str = ""  # proposal | rejected | no_change | error
    rejection_reason: str = ""
    attempts: int = 0
    explanation: str = ""
    code: str = ""

    targets_expected: int | None = None
    targets_actual: int = 0
    targets_match: bool | None = None
    targets_detail: str = ""

    diff_match: bool | None = None
    diff_detail: str = ""
    flagged_rows: int = 0

    integrity_ok: bool | None = None
    integrity_detail: str = ""

    duration_seconds: float = 0.0
    error: str = ""
    #: How many times the case actually ran under ``--repeat``.
    runs: int = 1

    @property
    def passed(self) -> bool:
        """Advisory cases never fail; otherwise the case's kind decides."""
        if self.advisory:
            return True
        if self.error:
            return False
        if self.kind == "reject":
            return self.outcome in ("rejected", "no_change") and self.targets_match is not False
        if self.kind == "no_change":
            return self.outcome == "no_change"
        return (
            self.outcome == "proposal"
            and self.targets_match is not False
            and self.diff_match is not False
            and self.integrity_ok is not False
        )

    @property
    def repairs(self) -> int:
        return max(0, self.attempts - 1)

    def as_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "section": self.section_number,
            "prompt": self.prompt,
            "expectation": self.expectation,
            "kind": self.kind,
            "advisory": self.advisory,
            "outcome": self.outcome,
            "rejection_reason": self.rejection_reason,
            "attempts": self.attempts,
            "explanation": self.explanation,
            "code": self.code,
            "targets_expected": self.targets_expected,
            "targets_actual": self.targets_actual,
            "targets_match": self.targets_match,
            "targets_detail": self.targets_detail,
            "diff_match": self.diff_match,
            "diff_detail": self.diff_detail,
            "flagged_rows": self.flagged_rows,
            "integrity_ok": self.integrity_ok,
            "integrity_detail": self.integrity_detail,
            "passed": self.passed,
            "duration_seconds": round(self.duration_seconds, 3),
            "error": self.error,
            "runs": self.runs,
        }


class BenchmarkRunner:
    """Executes corpus cases against one project and one model."""

    def __init__(self, project, *, ifc_file, user=None) -> None:
        self.project = project
        self.ifc_file = ifc_file
        self.user = user
        self.pipeline = ModifyPipeline(project, user=user)

    def run_case(self, case: BenchmarkCase) -> CaseResult:
        """Run one case end to end and score it."""
        result = CaseResult(
            case_id=case.id,
            section_number=case.section_number,
            prompt=case.prompt,
            expectation=case.describe_expectation(),
            kind=case.kind,
            advisory=case.advisory,
        )
        emitter = CapturingEmitter()
        started = time.perf_counter()
        prompt = case.prompt
        if case.guid_source is not None:
            resolved = resolve_targets(self.ifc_file, case.guid_source)
            if len(resolved) != 1:
                result.duration_seconds = time.perf_counter() - started
                result.outcome = "error"
                result.error = (
                    f"guid: expected exactly 1 entity for {case.guid_source.describe()}, "
                    f"found {len(resolved)}"
                )
                return result
            prompt = prompt.replace("{GUID}", next(iter(resolved)))
        try:
            outcome = self.pipeline.run(prompt, ifc_file=self.ifc_file, emitter=emitter)
        except NoChangeError as e:
            result.duration_seconds = time.perf_counter() - started
            result.outcome = "no_change"
            result.rejection_reason = str(e)
            result.code = e.code
            result.attempts = _attempts(emitter)
            self._score_targets(case, result, e.targets)
            if case.kind == "change":
                # The file was left as it was: a failed selection and a failed
                # diff, in both denominators, like a rejection (spec B-2).
                result.targets_detail = f"already so: {result.targets_detail}"
                if case.expect_targets:
                    result.targets_match = False
                if case.expect_diff:
                    result.diff_match = False
            return result
        except ModelUnavailableError as e:
            # The model did not answer: a harness/provider failure, never a
            # refusal the reject column could take credit for.
            result.duration_seconds = time.perf_counter() - started
            result.outcome = "error"
            result.error = f"{type(e).__name__}: {e}"
            result.attempts = _attempts(emitter)
            return result
        except ModificationError as e:
            result.duration_seconds = time.perf_counter() - started
            result.outcome = "rejected"
            result.rejection_reason = str(e)
            result.code = getattr(e, "code", "")
            result.attempts = _attempts(emitter)
            self._score_rejection(case, result)
            return result
        except Exception as e:  # noqa: BLE001 — one bad case must not end the run
            result.duration_seconds = time.perf_counter() - started
            result.outcome = "error"
            result.error = f"{type(e).__name__}: {e}"
            logger.warning("Case %s raised: %s", case.id, e, exc_info=True)
            return result

        result.duration_seconds = time.perf_counter() - started
        result.outcome = "proposal"
        result.attempts = outcome.attempts
        result.explanation = outcome.explanation
        result.code = outcome.code
        result.flagged_rows = outcome.flagged_count
        self._score_integrity(result, outcome)
        Path(outcome.scratch_path).unlink(missing_ok=True)

        if case.kind == "reject":
            result.targets_detail = "a proposal was produced for a request that must be declined"
        self._score_targets(case, result, outcome.targets)
        self._score_diff(case, result, outcome.rows)
        return result

    # ── Scoring ────────────────────────────────────────────

    def _score_rejection(self, case: BenchmarkCase, result: CaseResult) -> None:
        if case.kind != "reject":
            # A change case that ended in a rejection failed its selection and
            # its diff; it stays in both denominators.
            result.targets_detail = f"got a rejection: {result.rejection_reason[:200]}"
            if case.expect_targets:
                result.targets_match = False
            if case.expect_diff:
                result.diff_match = False
            return
        if not case.expect_reject_substrings:
            result.targets_detail = "rejected"
            return
        reason = result.rejection_reason.casefold()
        hit = next((s for s in case.expect_reject_substrings if s.casefold() in reason), None)
        if hit:
            result.targets_detail = f"rejected, reason mentions {hit!r}"
        else:
            result.targets_match = False
            alternatives = " / ".join(repr(s) for s in case.expect_reject_substrings)
            result.targets_detail = f"rejected, but the reason mentions none of {alternatives}"

    def _score_targets(self, case: BenchmarkCase, result: CaseResult, actual: list[str]) -> None:
        result.targets_actual = len(actual)
        if not case.expect_targets:
            return
        expected: set[str] = set()
        for expectation in case.expect_targets:
            resolved = resolve_targets(self.ifc_file, expectation)
            if len(resolved) != expectation.count:
                result.targets_match = False
                result.targets_detail = (
                    f"corpus says {expectation.describe()} but the index resolves it to "
                    f"{len(resolved)} entities"
                )
                return
            expected |= resolved
        result.targets_expected = len(expected)
        actual_set = set(actual)
        result.targets_match = actual_set == expected
        if result.targets_match:
            result.targets_detail = f"{len(expected)} target(s) as expected"
            return
        missing, extra = expected - actual_set, actual_set - expected
        result.targets_detail = (
            f"expected {len(expected)}, got {len(actual_set)}: "
            f"{len(missing)} missing, {len(extra)} unexpected"
        )

    def _score_diff(self, case: BenchmarkCase, result: CaseResult, rows: list[DiffRow]) -> None:
        if not case.expect_diff:
            return
        failures = []
        for expectation in case.expect_diff:
            found = _count_matching(rows, expectation)
            if found != expectation.count:
                failures.append(f"{expectation.describe()}: found {found}")
        result.diff_match = not failures
        result.diff_detail = (
            f"{len(case.expect_diff)} expectation(s) met" if not failures else "; ".join(failures)
        )

    def _score_integrity(self, result: CaseResult, outcome: PipelineOutcome) -> None:
        """Re-read the written scratch copy and diff it against the original on disk."""
        try:
            written = diff_files(self.ifc_file.file.path, outcome.scratch_path)
        except Exception as e:  # noqa: BLE001 — an unreadable copy is an integrity failure, not a crash
            result.integrity_ok = False
            result.integrity_detail = f"could not re-read the scratch copy: {e}"
            return
        problems = written.unexpected(allowed=set(outcome.targets), allow_population_change=True)
        result.integrity_ok = not problems
        result.integrity_detail = (
            "; ".join(problems[:3])
            if problems
            else "the written copy changes nothing outside the selection"
        )


# ── Resolution and matching ────────────────────────────────────────


def resolve_targets(ifc_file, expectation: TargetExpectation) -> set[str]:
    """GlobalIds the expectation names, read from the index."""
    schema = ifc_file.schema_version or "IFC4"
    types = set(expand_type(expectation.ifc_type, schema)) | {expectation.ifc_type}
    queryset = IFCEntity.objects.filter(ifc_file=ifc_file, ifc_type__in=types)
    if expectation.container:
        queryset = queryset.filter(
            Q(spatial_container__entity__name__iexact=expectation.container)
            | Q(spatial_container__parent__entity__name__iexact=expectation.container)
        )
    if expectation.named:
        queryset = queryset.filter(name__icontains=expectation.named)
    if not expectation.where_key:
        return set(queryset.values_list("global_id", flat=True))
    pset, _, prop = expectation.where_key.partition(".")
    return {
        entity.global_id
        for entity in queryset.only("global_id", "properties")
        if _value_matches(get_prop(entity.properties or {}, pset, prop), expectation.where_value)
    }


def _count_matching(rows: list[DiffRow], expectation: DiffExpectation) -> int:
    total = 0
    for row in rows:
        if _row_matches(row, expectation):
            total += row.count
    return total


def _row_matches(row: DiffRow, expectation: DiffExpectation) -> bool:
    kind = expectation.kind
    if kind in ("add_entity", "remove_entity"):
        wanted = "added" if kind == "add_entity" else "removed"
        return row.kind == wanted and row.prop.casefold() == expectation.prop.casefold()
    if row.kind not in ("property", "attribute"):
        return False
    if row.prop.casefold() != expectation.prop.casefold():
        return False
    if expectation.pset not in ("", "*") and row.pset.casefold() != expectation.pset.casefold():
        return False
    if expectation.pset == "" and row.pset:
        return False
    if kind == "remove":
        return row.after is None
    return _value_matches(row.after, expectation.value)


def _value_matches(actual, expected: str, *, loose: bool = False) -> bool:
    """True when ``actual`` is the value the corpus names.

    Scalars must be equal (case-insensitive; numbers within a relative
    1e-6, whether the file stores ``30``, ``30.0`` or ``"30"``), so an
    expected ``EI60`` is not satisfied by ``REI60``. Only the items of a
    list value (materials, groups) are matched loosely, by containment.
    """
    wanted = expected.strip().casefold()
    if isinstance(actual, bool):
        return wanted in ("true", "false") and (wanted == "true") == actual
    if actual is None:
        return wanted in ("none", "null", "")
    if isinstance(actual, (list, tuple)):
        return any(_value_matches(item, expected, loose=True) for item in actual)
    text = str(actual).strip().casefold()
    try:
        return abs(float(text) - float(wanted)) <= 1e-6 * max(1.0, abs(float(wanted)))
    except ValueError:
        pass
    return text == wanted or (loose and wanted in text)


def _attempts(emitter: CapturingEmitter) -> int:
    return sum(1 for e in emitter.events if e["phase"] == "generate" and e["status"] == "running")
