# writeback/services/pipeline.py
"""The Modify pipeline: ground → generate → run → verify.

One path for every request. The model writes ``select`` and ``modify``; a child
process runs them on a scratch copy and returns the selection and the measured
diff; the deterministic checks decide whether the result is a proposal, a
repair, or a rejection. Two model calls per request (code, blind explanation);
Guardian runs later, in :class:`ProposalService`.

One repair rule (spec C-4): every failure — no code block, a traceback, zero
targets, a scope violation — is one error string fed back with the previous
code. At most two repairs; a third failure raises :class:`ModificationError`
quoting the last error. A ``REJECT:`` answer (a greeting, a question, geometry)
is not a failure: it ends the request at once, with the model's reason. The
scratch copy of a successful run is kept for the proposal; every other scratch
copy is deleted here.
"""

from __future__ import annotations

import logging
import shutil
import uuid
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from ifc_processor.models import IFCEntity, IFCFile
from ifc_processor.services.code_sandbox import CodeSandboxError, run_code_subprocess
from ifc_processor.services.fingerprint import compute_fingerprint

from .emitters import NullEmitter, Phase, PipelineEmitter
from .errors import ModificationError, NoChangeError
from .explainer import explain
from .generator import CodeGenerator, extract_code_block, extract_rejection
from .grounding import Grounding, build_grounding
from .verifier import DiffRow, aggregate_rows, flag_rows, is_empty, scope_error

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3

EXTRACTION_ERROR = (
    "The response did not contain one fenced Python block defining "
    "select(model) and modify(model, targets)."
)
ZERO_TARGETS_ERROR = (
    "select(model) selected no entities. Use the storey, space, type and "
    "property-set names exactly as listed in the model facts."
)


@dataclass
class PipelineOutcome:
    """What a successful run produced; the proposal row stores all of it."""

    ifc_file: IFCFile
    code: str
    targets: list[str]
    diff: dict
    rows: list[DiffRow]
    scratch_path: str
    base_fingerprint: str
    explanation: str
    explainer_model: str
    attempts: int
    grounding: Grounding
    target_summary: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def flagged_count(self) -> int:
        return sum(1 for row in self.rows if row.flagged)


class ModifyPipeline:
    """Runs one request from text to a verified scratch copy."""

    def __init__(self, project, user=None, generator: CodeGenerator | None = None) -> None:
        self.project = project
        self.user = user
        self.generator = generator or CodeGenerator(user)

    def run(
        self,
        request: str,
        *,
        ifc_file: IFCFile | None = None,
        emitter: PipelineEmitter | None = None,
    ) -> PipelineOutcome:
        """Ground, then up to three generate → run → verify attempts.

        Raises:
            NoChangeError:     targets were selected but the file is already so.
            ModificationError: no processed file, or three failed attempts.
        """
        emitter = emitter or NullEmitter()
        ifc_file = ifc_file or self._resolve_ifc_file()
        if not IFCEntity.objects.filter(ifc_file=ifc_file).exists():
            raise ModificationError("No processed IFC entities found in this project.")

        emitter.emit(Phase.GROUND, "running", "Reading the model index…")
        grounding = build_grounding(ifc_file, request)
        emitter.emit(
            Phase.GROUND,
            "done",
            f"{grounding.storey_count} storeys, {grounding.space_count} spaces",
        )
        base_fingerprint = compute_fingerprint(ifc_file.file.path)

        code: str = ""
        error: str = ""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            outcome, code, error = self._attempt(
                attempt, request, ifc_file, grounding, base_fingerprint, code, error, emitter
            )
            if outcome is not None:
                return outcome

        message = (
            f"Could not produce a valid change after {MAX_ATTEMPTS} attempts. Last error: {error}"
        )
        raise ModificationError(
            message, failure_record_id=self._record_failure(message, request, code), code=code
        )

    # ── One attempt ────────────────────────────────────────

    def _attempt(
        self,
        attempt: int,
        request: str,
        ifc_file: IFCFile,
        grounding: Grounding,
        base_fingerprint: str,
        prior_code: str,
        prior_error: str,
        emitter: PipelineEmitter,
    ) -> tuple[PipelineOutcome | None, str, str]:
        """Returns ``(outcome, code, error)``; outcome is None when the attempt failed."""
        label = "Writing the change as code…" if attempt == 1 else f"Repairing (attempt {attempt})…"
        emitter.emit(Phase.GENERATE, "running", label)
        try:
            raw = (
                self.generator.generate(request, grounding)
                if attempt == 1
                else self.generator.repair(request, grounding, prior_code, prior_error)
            )
        except ModificationError as e:
            # A model timeout or an unreachable provider: a visible failure, not a repair.
            if e.failure_record_id is None:
                e.failure_record_id = self._record_failure(str(e), request)
            emitter.emit(Phase.GENERATE, "error", str(e))
            raise
        reason = extract_rejection(raw)
        if reason:
            message = f"Declined: {reason}"
            emitter.emit(Phase.GENERATE, "error", message)
            raise ModificationError(
                message, failure_record_id=self._record_failure(message, request)
            )
        code = extract_code_block(raw)
        if code is None:
            emitter.emit(Phase.GENERATE, "error", EXTRACTION_ERROR)
            return None, raw.strip()[:4000], EXTRACTION_ERROR
        emitter.emit(Phase.GENERATE, "done", f"{len(code.splitlines())} lines of code")

        emitter.emit(Phase.RUN, "running", "Running on a scratch copy…")
        scratch = self._make_scratch(ifc_file)
        try:
            return self._run_and_verify(
                attempt, request, ifc_file, grounding, base_fingerprint, code, scratch, emitter
            )
        except BaseException:
            # Cancellation, the no-change outcome or a crash: the copy has no owner yet.
            scratch.unlink(missing_ok=True)
            raise

    def _run_and_verify(
        self,
        attempt: int,
        request: str,
        ifc_file: IFCFile,
        grounding: Grounding,
        base_fingerprint: str,
        code: str,
        scratch: Path,
        emitter: PipelineEmitter,
    ) -> tuple[PipelineOutcome | None, str, str]:
        """Run the block on ``scratch`` and check the result; a failed check deletes the copy."""
        try:
            result = run_code_subprocess(scratch, code)
        except CodeSandboxError as e:
            scratch.unlink(missing_ok=True)
            emitter.emit(Phase.RUN, "error", str(e))
            return None, code, str(e)

        targets, diff = result["targets"], result["diff"]
        emitter.emit(Phase.RUN, "done", f"{len(targets)} target(s)", {"targets": len(targets)})

        emitter.emit(Phase.VERIFY, "running", "Checking scope…")
        error = ZERO_TARGETS_ERROR if not targets else scope_error(diff, targets)
        if error:
            scratch.unlink(missing_ok=True)
            emitter.emit(Phase.VERIFY, "error", error)
            return None, code, error
        if is_empty(diff):
            raise NoChangeError(
                f"Already so: the {len(targets)} selected entities already have the requested values.",
                targets=targets,
                code=code,
            )

        rows = flag_rows(aggregate_rows(diff), request)
        flags = sum(1 for row in rows if row.flagged)
        emitter.emit(
            Phase.VERIFY,
            "done",
            f"{len(rows)} change row(s), {flags} flagged",
            {"targets": len(targets), "flags": flags},
        )

        summary = self._target_summary(ifc_file, targets)
        explanation = explain(code, rows, summary, user=self.user)
        return (
            PipelineOutcome(
                ifc_file=ifc_file,
                code=code,
                targets=targets,
                diff=diff,
                rows=rows,
                scratch_path=str(scratch),
                base_fingerprint=base_fingerprint,
                explanation=explanation.text,
                explainer_model=explanation.model,
                attempts=attempt,
                grounding=grounding,
                target_summary=summary,
            ),
            code,
            "",
        )

    # ── Internals ──────────────────────────────────────────

    def _record_failure(self, message: str, request: str, code: str = "") -> str | None:
        """Persist the rejection as a FailureRecord for the failure card; never raises.

        ``code`` is the last block the model wrote, kept on the record so a live
        failure can be read back instead of guessed at.
        """
        try:
            from metacastor.services.failure_classifier import create_failure_record

            record = create_failure_record(
                ModificationError(message),
                phase="GENERATE",
                project=self.project,
                query_text=request,
                ifc_context={"code": code} if code else None,
            )
            return str(record.id) if record else None
        except Exception as e:  # noqa: BLE001 — the rejection itself must still reach the user
            logger.warning("Could not record the failure: %s", e)
            return None

    def _resolve_ifc_file(self) -> IFCFile:
        ifc_file = (
            IFCFile.objects.filter(project=self.project, status="completed")
            .order_by("-created_at")
            .first()
        )
        if ifc_file is None or not ifc_file.file:
            raise ModificationError("No processed IFC file found in this project.")
        return ifc_file

    @staticmethod
    def _make_scratch(ifc_file: IFCFile) -> Path:
        original = Path(ifc_file.file.path)
        scratch = original.with_name(
            f".{original.stem}.proposal-{uuid.uuid4().hex[:8]}{original.suffix}"
        )
        shutil.copy2(original, scratch)
        return scratch

    @staticmethod
    def _target_summary(ifc_file: IFCFile, targets: list[str]) -> str:
        counts = Counter(
            IFCEntity.objects.filter(ifc_file=ifc_file, global_id__in=targets).values_list(
                "ifc_type", flat=True
            )
        )
        parts = [f"{n} × {ifc_type}" for ifc_type, n in sorted(counts.items())]
        unknown = len(targets) - sum(counts.values())
        if unknown:
            parts.append(f"{unknown} not in the index")
        return ", ".join(parts) or f"{len(targets)} entities"
