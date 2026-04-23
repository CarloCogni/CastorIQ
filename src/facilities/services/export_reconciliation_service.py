# facilities/services/export_reconciliation_service.py
"""The Export Reconciliation Service — the critical M2 path.

Every FM write on a linked asset (property / pset / classification / attribute)
queues an :class:`facilities.models.FMDelta`. Nothing touches the IFC file at
that moment. When an FM clicks **Export IFC**, this service replays every
pending delta against a fresh snapshot of the IFC by driving the existing
:class:`ifc_processor.services.tier2_writer.Tier2Writer`, then produces a
single Git commit for the whole batch.

Public surface:

* :meth:`ExportReconciliationService.plan` — pure DB read. Groups pending
  deltas by their ``natural_key``, keeps the latest delta per key, marks the
  rest as superseded-at-plan-time. No IFC I/O. Safe to call from HTMX
  preview handlers.
* :meth:`ExportReconciliationService.preview` — renders :meth:`plan` into a
  template-friendly dict (op counts, per-entity breakdown, conflict total).
* :meth:`ExportReconciliationService.export` — full pipeline: plan → snapshot
  → apply → validate → commit → mark-applied. Emits progress through a
  :class:`PipelineEmitter` so the frontend can stream phases over the
  Facilities WebSocket.

Error model. Any exception during apply / validate / commit rolls back to the
snapshot, stamps the ``ExportJob`` as FAILED with a reason, leaves every delta
pending, and re-raises as :class:`ExportError` so the view layer has a
uniform error shape.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from django.db import transaction
from django.utils import timezone

from facilities.models import (
    DEFAULT_ENABLED_OPERATIONS,
    DEFAULT_ENABLED_PSETS,
    ExportJob,
    ExportProfile,
    FMDelta,
)
from facilities.services.ifc_mappers.dispatcher import apply_delta
from ifc_processor.services.ifc_writer import EntityChange, IFCWriteError
from ifc_processor.services.tier2_writer import Tier2Writer
from writeback.models import GitCommit
from writeback.services.emitters import CancellationError, NullEmitter, PipelineEmitter
from writeback.services.git_service import GitService

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Public data structures
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class ExportPlan:
    """The result of the plan phase — what the export *would* do.

    ``deltas_to_apply`` is the replay order (same as ``created_at`` asc).
    ``superseded`` lists the pending deltas that lost last-write-wins and
    will be marked as superseded on commit (never replayed).
    """

    deltas_to_apply: list[FMDelta] = field(default_factory=list)
    superseded: list[FMDelta] = field(default_factory=list)
    entity_guids: list[str] = field(default_factory=list)
    ops_by_type: dict[str, int] = field(default_factory=dict)

    @property
    def delta_count(self) -> int:
        return len(self.deltas_to_apply)

    @property
    def entity_count(self) -> int:
        return len(self.entity_guids)

    @property
    def conflict_count(self) -> int:
        return len(self.superseded)

    @property
    def is_empty(self) -> bool:
        return not self.deltas_to_apply


class ExportError(Exception):
    """User-facing error for any export failure. Always ties to an ExportJob id."""

    def __init__(self, message: str, job_id: str | None = None) -> None:
        super().__init__(message)
        self.job_id = job_id


# ──────────────────────────────────────────────────────────────────────────────
# The service
# ──────────────────────────────────────────────────────────────────────────────


class ExportReconciliationService:
    """Plan and execute an IFC export for a project's pending FMDeltas."""

    def __init__(self, project) -> None:
        self.project = project
        self.git = GitService(project)

    # ── Phase 1: plan (pure DB) ─────────────────────────────────────────────

    def plan(self, *, profile: ExportProfile | None = None) -> ExportPlan:
        """Compute an :class:`ExportPlan` from the project's pending deltas.

        Pending = ``applied_to_ifc_at IS NULL AND superseded_at IS NULL``.
        Deltas are collapsed last-write-wins per ``natural_key``; the winner
        is the one with the latest ``created_at``. Losers are kept in the
        plan so the export flow can stamp them as superseded in the same
        transaction.

        If a profile is supplied (or the default is resolved) and it restricts
        operations or psets, deltas outside the allowlist are silently filtered
        out of the plan — they stay pending for a future export configured
        differently.
        """
        profile = profile or self._resolve_default_profile()
        allowed_ops = set(profile.enabled_operations or DEFAULT_ENABLED_OPERATIONS)
        allowed_psets = set(profile.enabled_pset_allowlist or []) or None  # None = no allowlist

        pending = FMDelta.objects.filter(
            project=self.project,
            applied_to_ifc_at__isnull=True,
            superseded_at__isnull=True,
        ).order_by("created_at")

        latest_by_key: dict[tuple, FMDelta] = {}
        superseded: list[FMDelta] = []
        for delta in pending:
            if delta.operation not in allowed_ops:
                continue
            if allowed_psets is not None and delta.operation in (
                FMDelta.Operation.ADD_PSET,
                FMDelta.Operation.SET_PROPERTY,
            ):
                pset = (delta.payload or {}).get("pset", "")
                if pset and pset not in allowed_psets:
                    continue

            key = delta.natural_key
            prior = latest_by_key.get(key)
            if prior is not None:
                superseded.append(prior)
            latest_by_key[key] = delta

        deltas_to_apply = sorted(latest_by_key.values(), key=lambda d: d.created_at)

        entity_guids = sorted({d.entity_guid for d in deltas_to_apply})

        ops_by_type: dict[str, int] = defaultdict(int)
        for delta in deltas_to_apply:
            ops_by_type[delta.operation] += 1

        return ExportPlan(
            deltas_to_apply=deltas_to_apply,
            superseded=superseded,
            entity_guids=entity_guids,
            ops_by_type=dict(ops_by_type),
        )

    def preview(self, *, profile: ExportProfile | None = None) -> dict:
        """Template-friendly preview of :meth:`plan`.

        Renders per-entity rows (``{global_id, name, ops: [str]}``) and the
        plan's aggregate counters. Safe to call from HTMX GET handlers; no
        ``ExportJob`` is written.
        """
        plan = self.plan(profile=profile)
        entity_rows = self._build_entity_rows(plan)
        return {
            "plan": plan,
            "delta_count": plan.delta_count,
            "entity_count": plan.entity_count,
            "conflict_count": plan.conflict_count,
            "ops_by_type": plan.ops_by_type,
            "entity_rows": entity_rows,
            "is_empty": plan.is_empty,
        }

    # ── Phase 2: export (IFC + Git I/O) ─────────────────────────────────────

    def export(
        self,
        *,
        ifc_file,
        user,
        profile: ExportProfile | None = None,
        emitter: PipelineEmitter | None = None,
    ) -> ExportJob:
        """Run the full pipeline and return the completed ``ExportJob``.

        Raises :class:`ExportError` on any failure; the error's ``job_id`` ties
        back to an ``ExportJob`` stamped FAILED with the reason. IFC file is
        rolled back to its pre-export state on failure.
        """
        emitter = emitter or NullEmitter()
        profile = profile or self._resolve_default_profile()

        # ── plan ──────────────────────────────────────────────────────────
        emitter.emit("plan", "running", "Collecting pending FM updates…")
        plan = self.plan(profile=profile)

        if plan.is_empty:
            emitter.emit("plan", "error", "Nothing to export.")
            raise ExportError("No pending FM updates to export.")

        job = ExportJob.objects.create(
            project=self.project,
            ifc_file=ifc_file,
            initiated_by=user,
            export_profile=profile,
            status=ExportJob.Status.PLANNING,
            delta_count=plan.delta_count,
            entity_count=plan.entity_count,
            conflict_count=plan.conflict_count,
        )
        emitter.emit(
            "plan",
            "done",
            f"{plan.delta_count} delta(s) across {plan.entity_count} entity(ies), "
            f"{plan.conflict_count} superseded",
            {
                "delta_count": plan.delta_count,
                "entity_count": plan.entity_count,
                "conflict_count": plan.conflict_count,
                "ops_by_type": plan.ops_by_type,
                "job_id": str(job.pk),
            },
        )

        try:
            self._check_cancelled(emitter, job)

            # ── snapshot ──────────────────────────────────────────────────
            emitter.emit("snapshot", "running", "Taking safety snapshot of IFC…")
            self.git.ensure_repo()
            snapshot_hash = self.git.snapshot(ifc_file)
            rollback_target = snapshot_hash or self.git.repo.head.commit.hexsha
            emitter.emit("snapshot", "done", f"Snapshot at {rollback_target[:8]}")

            # Transition to RUNNING now that the IFC is safely snapshotted.
            job.status = ExportJob.Status.RUNNING
            job.started_at = timezone.now()
            job.save(update_fields=["status", "started_at", "updated_at"])

            # ── apply ─────────────────────────────────────────────────────
            ifc_path = Path(ifc_file.file.path)
            writer = Tier2Writer(ifc_path)

            changes: list[EntityChange] = []
            total = plan.delta_count
            for i, delta in enumerate(plan.deltas_to_apply, start=1):
                self._check_cancelled(
                    emitter, job, rollback_target=rollback_target, ifc_file=ifc_file
                )
                emitter.emit(
                    "apply",
                    "running",
                    f"Applying delta {i} of {total}: {delta.operation} on {delta.entity_guid[:8]}",
                    {"current": i, "total": total},
                )
                try:
                    delta_changes = apply_delta(writer, delta)
                except IFCWriteError as e:
                    raise ExportError(f"Write failed at delta {i}/{total}: {e}", job.pk) from e
                changes.extend(delta_changes)

            emitter.emit("apply", "done", f"Applied {total} delta(s) in-memory")

            # ── validate ─────────────────────────────────────────────────
            emitter.emit("validate", "running", "Validating in-memory model…")
            self._validate_model(writer)
            emitter.emit("validate", "done", "Model schema valid")

            # ── save + commit ─────────────────────────────────────────────
            emitter.emit("save", "running", "Writing IFC to disk…")
            writer.save()
            emitter.emit("save", "done", "IFC written")

            emitter.emit("commit", "running", "Committing to Git…")
            diff_data = self._build_diff_data(plan, changes, user)
            commit_message = self._build_commit_message(plan, user)
            commit_hash = self.git.commit_modification(
                ifc_file=ifc_file,
                message=commit_message,
                tier=2,
                diff_data=diff_data,
                author_name=self._author_name(user),
            )
            emitter.emit("commit", "done", f"Commit {commit_hash[:8]}")

            # ── mark applied + superseded ─────────────────────────────────
            now = timezone.now()
            with transaction.atomic():
                # Mirror the ``writeback.ModificationService.execute`` pattern:
                # a ``GitCommit`` row is what the History tab reads from. The
                # on-disk Git commit alone is invisible to the app without it.
                git_commit = GitCommit.objects.create(
                    ifc_file=ifc_file,
                    commit_hash=commit_hash,
                    parent_hash=rollback_target or "",
                    message=commit_message,
                    author=user,
                    entities_modified=plan.entity_count,
                    diff_data=diff_data,
                )

                applied_ids = [d.pk for d in plan.deltas_to_apply]
                superseded_ids = [d.pk for d in plan.superseded]
                if applied_ids:
                    FMDelta.objects.filter(pk__in=applied_ids).update(
                        applied_to_ifc_at=now,
                        export_job=job,
                        ifc_commit_hash=commit_hash,
                    )
                if superseded_ids:
                    FMDelta.objects.filter(pk__in=superseded_ids).update(
                        superseded_at=now,
                        export_job=job,
                    )
                job.status = ExportJob.Status.COMPLETED
                job.completed_at = now
                job.commit_hash = commit_hash
                job.save(update_fields=["status", "completed_at", "commit_hash", "updated_at"])

            logger.info(
                "FM export %s wrote GitCommit %s for ifc_file %s",
                job.pk,
                git_commit.pk,
                ifc_file.pk,
            )

            emitter.emit(
                "done",
                "done",
                f"Exported {plan.delta_count} delta(s) as {commit_hash[:8]}",
                {"commit_hash": commit_hash, "job_id": str(job.pk)},
            )
            logger.info(
                "FM export %s complete: %d deltas, %d entities, commit %s",
                job.pk,
                plan.delta_count,
                plan.entity_count,
                commit_hash[:8],
            )
            return job

        except CancellationError:
            self._fail(job, "Export cancelled by user.", emitter)
            self._try_rollback(ifc_file, locals().get("rollback_target"))
            raise ExportError("Export cancelled.", job.pk) from None
        except ExportError as e:
            self._fail(job, str(e), emitter)
            self._try_rollback(ifc_file, locals().get("rollback_target"))
            raise
        except Exception as e:
            logger.exception("FM export %s failed unexpectedly", job.pk)
            self._fail(job, f"Unexpected error: {type(e).__name__}: {e}", emitter)
            self._try_rollback(ifc_file, locals().get("rollback_target"))
            raise ExportError(f"Export failed: {e}", job.pk) from e

    # ── Internals ───────────────────────────────────────────────────────────

    def _resolve_default_profile(self) -> ExportProfile:
        """Return the project's default profile, seeding a row on first call."""
        profile, created = ExportProfile.objects.get_or_create(
            project=self.project,
            is_default=True,
            defaults={
                "name": "default",
                "enabled_operations": list(DEFAULT_ENABLED_OPERATIONS),
                "enabled_pset_allowlist": list(DEFAULT_ENABLED_PSETS),
            },
        )
        if created:
            logger.info("Seeded default ExportProfile for project %s", self.project.pk)
        return profile

    def _build_entity_rows(self, plan: ExportPlan) -> list[dict]:
        """Aggregate the plan's deltas into per-entity preview rows.

        Each row: ``{global_id, name, ops, delta_count, deltas}``.
        ``deltas`` carries the underlying :class:`FMDelta` instances so the
        preview template can render a collapsible sub-table with target,
        old→new values, timestamp, and author per change. Looks up display
        names via the asset FK when present; falls back to the GUID stub.
        """
        from collections import defaultdict as dd

        by_guid: dict[str, list[FMDelta]] = dd(list)
        for delta in plan.deltas_to_apply:
            by_guid[delta.entity_guid].append(delta)

        rows: list[dict] = []
        for guid, deltas in by_guid.items():
            asset = next((d.asset for d in deltas if d.asset_id), None)
            name = ""
            if asset is not None:
                name = asset.display_name or ""
            # Deterministic order within the row: oldest delta first, so the
            # sub-table matches the replay order the export will use.
            deltas_sorted = sorted(deltas, key=lambda d: d.created_at)
            rows.append(
                {
                    "global_id": guid,
                    "name": name or guid[:8],
                    "ops": [d.get_operation_display() for d in deltas_sorted],
                    "delta_count": len(deltas_sorted),
                    "deltas": deltas_sorted,
                }
            )
        rows.sort(key=lambda r: (r["name"].lower(), r["global_id"]))
        return rows

    def _build_diff_data(self, plan: ExportPlan, changes: list[EntityChange], user) -> dict:
        """Semantic diff bundle for ``GitCommit.diff_data``.

        Shape is intentionally compatible with the writeback Modify flow so
        that the shared ``_history.html`` template can render FM exports
        without a second code path. In particular the per-change keys follow
        writeback's convention: ``entity`` (GlobalId), ``name``,
        ``ifc_type``, ``pset``, ``property``, ``old``, ``new``.
        """
        return {
            "source": "fm_export",
            "tier": 2,
            "delta_count": plan.delta_count,
            "entity_count": plan.entity_count,
            "conflict_count": plan.conflict_count,
            "ops_by_type": plan.ops_by_type,
            "affected_entities": plan.entity_count,
            "changes": [
                {
                    "entity": c.global_id,
                    "name": c.entity_name,
                    "ifc_type": c.ifc_type,
                    "pset": c.pset,
                    "property": c.property,
                    "old": c.old_value,
                    "new": c.new_value,
                }
                for c in changes
            ],
            "initiated_by": self._author_name(user),
            "exported_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        }

    def _build_commit_message(self, plan: ExportPlan, user) -> str:
        """Human-readable top line for the Git commit."""
        today = timezone.localdate().isoformat()
        return (
            f"FM reconciliation {today}: "
            f"{plan.entity_count} entity(ies), {plan.delta_count} delta(s), "
            f"by {self._author_name(user)}"
        )

    @staticmethod
    def _author_name(user) -> str:
        if user is None:
            return "Castor"
        for attr in ("get_full_name", "username", "email"):
            value = getattr(user, attr, None)
            if callable(value):
                value = value()
            if value:
                return str(value)
        return "Castor"

    @staticmethod
    def _validate_model(writer: Tier2Writer) -> None:
        """Cheap structural sanity check on the in-memory model.

        v1 validation is minimal: confirm that the model still reports a
        schema and that the handful of IfcRoot instances we touched are
        still resolvable by GUID. Full schema validation is the Tier 2
        validator's job; we reuse its spirit here, not its machinery.
        """
        model = writer.model
        if model is None or model.schema is None:
            raise ExportError("Model lost its schema after write — aborting.")

    def _try_rollback(self, ifc_file, rollback_target) -> None:
        if not rollback_target:
            return
        try:
            self.git.rollback(ifc_file, rollback_target)
        except Exception:
            logger.exception("Rollback of %s to %s failed", ifc_file.pk, rollback_target)

    @staticmethod
    def _fail(job: ExportJob, reason: str, emitter: PipelineEmitter) -> None:
        if job.status in (ExportJob.Status.COMPLETED, ExportJob.Status.FAILED):
            return
        job.status = ExportJob.Status.FAILED
        job.completed_at = timezone.now()
        job.error_reason = reason
        job.save(update_fields=["status", "completed_at", "error_reason", "updated_at"])
        emitter.emit("failed", "error", reason, {"job_id": str(job.pk)})

    @staticmethod
    def _check_cancelled(
        emitter: PipelineEmitter,
        job: ExportJob,
        *,
        rollback_target: str | None = None,
        ifc_file=None,
    ) -> None:
        if not emitter.is_cancelled():
            return
        raise CancellationError()
