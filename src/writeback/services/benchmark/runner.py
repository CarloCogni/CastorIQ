# writeback/services/benchmark/runner.py
"""Run corpus cases through the real pipeline and score the outcome.

One case is one full trip: natural language → the V2 pipeline → a mutation
journal → executed against a **scratch copy** of the project's IFC → the file
read back to confirm the change is really there.

Two isolation guarantees make a 92-case run safe and repeatable:

* **The real IFC is never touched.** Every case executes against its own copy
  inside a temporary directory. ``JournalExecutor`` is only ever constructed
  with a path under that directory.
* **Cases do not contaminate each other.** The copy is fresh per case and the
  DB index is never re-synced, so every case sees the same baseline model.
  This matters more than it looks: several corpus cases assert the
  ``SET_PROPERTY`` → ``ADD_PROPERTY`` fallback, which only fires while
  ``FireRating`` is still absent — one leaked write would silently invalidate
  them.

No proposal is ever persisted: ``ProposalPipeline.run()`` stops at the outcome,
and creating the ``ModificationProposal`` row is a separate service's job.
"""

from __future__ import annotations

import logging
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory

from ifc_processor.services.ifc_diff import IfcSnapshot, diff_snapshots
from ifc_processor.services.journal import MutationJournal, MutationOp
from ifc_processor.services.journal_executor import JournalExecutor

from ..emitters import CapturingEmitter
from ..errors import ModificationError
from ..proposal_pipeline import ProposalPipeline
from .corpus import BenchmarkCase
from .verify import FidelityCheck, verify_journal

logger = logging.getLogger(__name__)


@dataclass
class CaseResult:
    """What happened when one case ran once."""

    case_id: str
    section_number: str
    prompt: str
    expectation: str
    advisory: bool

    # Understanding — did it route the way the corpus says?
    understood: bool = False
    actual_tier: int | None = None
    actual_operation: str = ""
    rejected: bool = False
    rejection_reason: str = ""
    understanding_detail: str = ""

    # Slots — a diagnostic, never a pass/fail gate (see `slots_note`).
    slots_match: bool | None = None
    slots_detail: str = ""

    # Fidelity — did the journal land in the file?
    executed: bool = False
    fidelity_checks: list[FidelityCheck] = field(default_factory=list)
    fidelity_ok: bool | None = None
    fidelity_detail: str = ""

    # Integrity — did anything *outside* the journal change in the file?
    integrity_ok: bool | None = None
    integrity_detail: str = ""

    duration_seconds: float = 0.0
    error: str = ""

    @property
    def passed(self) -> bool:
        """Advisory cases never fail; otherwise both scores must hold."""
        if self.advisory:
            return True
        return self.understood and self.fidelity_ok is not False and self.integrity_ok is not False

    def as_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "section": self.section_number,
            "prompt": self.prompt,
            "expectation": self.expectation,
            "advisory": self.advisory,
            "understood": self.understood,
            "actual_tier": self.actual_tier,
            "actual_operation": self.actual_operation,
            "rejected": self.rejected,
            "rejection_reason": self.rejection_reason,
            "understanding_detail": self.understanding_detail,
            "slots_match": self.slots_match,
            "slots_detail": self.slots_detail,
            "executed": self.executed,
            "fidelity_ok": self.fidelity_ok,
            "fidelity_detail": self.fidelity_detail,
            "fidelity_checks": [c.as_dict() for c in self.fidelity_checks],
            "integrity_ok": self.integrity_ok,
            "integrity_detail": self.integrity_detail,
            "duration_seconds": round(self.duration_seconds, 3),
            "error": self.error,
        }


class BenchmarkRunner:
    """Executes corpus cases against one project and one model."""

    def __init__(self, project, *, ifc_file, user=None, execute: bool = True) -> None:
        """
        Args:
            project:  The project whose DB index the pipeline resolves against.
            ifc_file: The IFCFile whose model the journals target.
            user:     Optional user, for per-user LLM config.
            execute:  False scores understanding only — no scratch copies, no
                      writes. Much faster for iterating on prompts.
        """
        self.project = project
        self.ifc_file = ifc_file
        self.user = user
        self.execute = execute
        self.pipeline = ProposalPipeline(project, user=user)
        # The source file never changes during a run, so its snapshot is taken
        # once and reused as the "before" side of every integrity diff.
        self._baseline: IfcSnapshot | None = None

    def run_case(self, case: BenchmarkCase) -> CaseResult:
        """Run one case end to end and score it."""
        result = CaseResult(
            case_id=case.id,
            section_number=case.section_number,
            prompt=case.prompt,
            expectation=case.describe_expectation(),
            advisory=case.advisory,
        )

        started = time.perf_counter()
        try:
            outcome = self.pipeline.run(
                case.prompt,
                user=self.user,
                ifc_file=self.ifc_file,
                emitter=CapturingEmitter(),
            )
        except ModificationError as e:
            result.duration_seconds = time.perf_counter() - started
            result.rejected = True
            result.actual_tier = 0
            result.rejection_reason = str(e)
            self._score_rejection(case, result)
            return result
        except Exception as e:  # noqa: BLE001 — one bad case must not end the run
            result.duration_seconds = time.perf_counter() - started
            result.error = f"{type(e).__name__}: {e}"
            result.understanding_detail = "pipeline raised an unexpected exception"
            logger.warning("Case %s raised: %s", case.id, e, exc_info=True)
            return result

        result.duration_seconds = time.perf_counter() - started
        result.actual_tier = outcome.tier
        result.actual_operation = outcome.operation
        self._score_routing(case, result)
        self._score_slots(case, result, outcome)

        if self.execute:
            self._execute_and_verify(outcome, result)
        return result

    # ── Understanding ──────────────────────────────────────

    def _score_rejection(self, case: BenchmarkCase, result: CaseResult) -> None:
        if not case.expects_rejection:
            result.understood = False
            result.understanding_detail = (
                f"expected {case.describe_expectation()}, got a tier 0 rejection"
            )
            return

        if not case.expect_reject_substrings:
            result.understood = True
            result.understanding_detail = "rejected (no substring required)"
            return

        reason = result.rejection_reason.casefold()
        # Alternatives are "any of", not "all of" — the corpus lists the
        # phrasings that would each be an acceptable explanation.
        hit = next(
            (s for s in case.expect_reject_substrings if s.casefold() in reason),
            None,
        )
        if hit:
            result.understood = True
            result.understanding_detail = f"rejected, reason mentions {hit!r}"
        else:
            result.understood = False
            alternatives = " / ".join(repr(s) for s in case.expect_reject_substrings)
            result.understanding_detail = f"rejected, but reason mentions none of {alternatives}"

    def _score_routing(self, case: BenchmarkCase, result: CaseResult) -> None:
        if case.expects_rejection:
            result.understood = False
            result.understanding_detail = (
                f"expected a tier 0 rejection, got tier {result.actual_tier}, "
                f"{result.actual_operation}"
            )
            return

        if case.expect_tier is None:
            result.understood = True
            result.understanding_detail = "no expectation declared"
            return

        tier_ok = result.actual_tier == case.expect_tier
        operation_ok = not case.expect_operation or result.actual_operation == case.expect_operation
        result.understood = tier_ok and operation_ok
        if result.understood:
            result.understanding_detail = f"tier {result.actual_tier}, {result.actual_operation}"
        else:
            result.understanding_detail = (
                f"expected {case.describe_expectation()}, got tier {result.actual_tier}, "
                f"{result.actual_operation}"
            )

    def _score_slots(self, case: BenchmarkCase, result: CaseResult, outcome) -> None:
        """Compare declared slots against the intent the pipeline built.

        Reported but never gating: the corpus writes slots as prose-ish
        pseudo-JSON and the pipeline may legitimately normalise a value
        (``0.18`` → ``0.18``, ``EI240`` → ``EI240``) or infer a pset the corpus
        left implicit. A mismatch is a hint for a human, not a verdict.
        """
        if not case.expect_slots:
            return

        intent = outcome.intent_json or {}
        mismatches = []
        for key, expected in case.expect_slots.items():
            actual = _lookup_intent(intent, key)
            if actual is None:
                continue
            if str(actual).strip().casefold() != str(expected).strip().casefold():
                mismatches.append(f"{key}: expected {expected!r}, got {actual!r}")

        result.slots_match = not mismatches
        result.slots_detail = "; ".join(mismatches)

    # ── Fidelity ───────────────────────────────────────────

    def _execute_and_verify(self, outcome, result: CaseResult) -> None:
        """Apply the journal to a throwaway copy and read the file back."""
        if not outcome.is_journal or not outcome.changes:
            result.fidelity_detail = "no journal to execute"
            return

        try:
            journal = MutationJournal.from_json_dict(outcome.changes)
        except Exception as e:  # noqa: BLE001
            result.fidelity_ok = False
            result.fidelity_detail = f"journal did not decode: {e}"
            return

        if not journal.mutations:
            result.fidelity_detail = "journal is empty"
            return

        source = Path(self.ifc_file.file.path)
        # ignore_cleanup_errors: on Windows a lingering handle (an ifcopenshell
        # file object, or a RUN_CODE child that outlived its timeout) makes
        # rmtree raise PermissionError. That would escape and kill a 92-case
        # run over a leftover temp file nobody needs.
        with TemporaryDirectory(prefix="castor-benchmark-", ignore_cleanup_errors=True) as tmp:
            # The executor swaps over whatever path it is given, so this must
            # be the copy — never `source`.
            scratch = Path(tmp) / source.name
            shutil.copy2(source, scratch)
            try:
                applied = JournalExecutor(scratch).apply(journal)
            except Exception as e:  # noqa: BLE001
                result.fidelity_ok = False
                result.fidelity_detail = f"execution failed: {type(e).__name__}: {e}"
                return

            result.executed = True
            result.fidelity_checks = verify_journal(applied, str(scratch))
            self._check_integrity(journal, scratch, result)

        scored = [c for c in result.fidelity_checks if not c.advisory]
        if not scored:
            result.fidelity_detail = "executed; nothing independently verifiable"
            return

        failures = [c for c in scored if not c.passed]
        result.fidelity_ok = not failures
        result.fidelity_detail = (
            f"{len(scored) - len(failures)}/{len(scored)} mutations verified"
            if not failures
            else "; ".join(f"{c.op}: {c.detail}" for c in failures[:3])
        )

    # ── Integrity ──────────────────────────────────────────

    def _check_integrity(self, journal: MutationJournal, scratch: Path, result: CaseResult) -> None:
        """Diff the written file against the untouched source.

        Anything the journal did not declare — geometry drift, a lost entity, a
        property changed on a bystander — is an integrity failure. Population
        changes are tolerated only when the journal contains an op that creates
        or deletes entities.
        """
        try:
            if self._baseline is None:
                self._baseline = IfcSnapshot.from_file(self.ifc_file.file.path)
            diff = diff_snapshots(self._baseline, IfcSnapshot.from_file(scratch))
        except Exception as e:  # noqa: BLE001
            result.integrity_ok = False
            result.integrity_detail = f"snapshot failed: {type(e).__name__}: {e}"
            return

        ops = {m.op for m in journal.mutations}
        problems = diff.unexpected(
            allowed=journal.affected_global_ids,
            allow_population_change=bool(ops & _POPULATION_OPS),
        )
        result.integrity_ok = not problems
        result.integrity_detail = (
            f"{len(diff.property_changes) + len(diff.attribute_changes)} change(s), "
            "all within journal"
            if not problems
            else "; ".join(problems[:3])
        )


_POPULATION_OPS = frozenset(
    {MutationOp.CREATE_ENTITY, MutationOp.DELETE_ENTITY, MutationOp.RUN_CODE}
)


def _lookup_intent(intent: dict, key: str):
    """Find a slot value in a T1 intent or a T2 plan's first step."""
    if key in intent:
        return intent[key]
    # T1 uses "new_value" where the corpus says "value".
    if key == "value" and "new_value" in intent:
        return intent["new_value"]
    for step in intent.get("plan") or []:
        params = step.get("params") or {}
        if key in params:
            return params[key]
        if key == "value" and "new_value" in params:
            return params["new_value"]
    return None
