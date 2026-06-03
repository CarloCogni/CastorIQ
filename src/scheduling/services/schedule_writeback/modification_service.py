# castor/scheduling/services/schedule_writeback/modification_service.py
"""Build and apply schedule modification proposals."""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass, field

from scheduling.models import Task

logger = logging.getLogger(__name__)

# Fields on Task that hold date values.
_DATE_FIELDS = frozenset({"start_date", "end_date", "actual_start", "actual_end"})

# Valid status values — guards against LLM drift (e.g. "completed" instead of "complete").
_VALID_STATUSES = frozenset(Task.Status.values)


@dataclass
class ModificationProposal:
    """A single proposed change to one Task."""

    task_id: str
    task_name: str
    activity_code: str
    changes: dict  # {field_name: {"from": old_val, "to": new_val}}
    action: (
        str  # UPDATE_DATE | UPDATE_STATUS | UPDATE_DURATION | ADD_DEPENDENCY | REMOVE_DEPENDENCY
    )
    dep_data: dict = field(default_factory=dict)

    def describe(self) -> str:
        lines = [f"**{self.task_name}** ({self.activity_code or '—'})"]
        for fname, change in self.changes.items():
            lines.append(f"  • {fname}: {change['from']} → **{change['to']}**")
        return "\n".join(lines)


class ScheduleModificationService:
    """Build and apply :class:`ModificationProposal` objects."""

    def build_proposals(self, tasks: list, slots: dict, kind: str) -> list[ModificationProposal]:
        """Compute what would change for each task and return proposals."""
        proposals: list[ModificationProposal] = []
        for task in tasks:
            changes = self._compute_changes(task, slots, kind)
            if changes:
                proposals.append(
                    ModificationProposal(
                        task_id=str(task.pk),
                        task_name=task.name,
                        activity_code=task.activity_code or "",
                        changes=changes,
                        action=kind,
                    )
                )
        return proposals

    def apply(self, proposals: list[ModificationProposal]) -> dict:
        """Persist approved proposals. Returns {updated, errors}."""
        updated = 0
        errors: list[str] = []

        for proposal in proposals:
            try:
                task = Task.objects.get(pk=proposal.task_id)
                update_fields: list[str] = []

                for fname, change in proposal.changes.items():
                    new_val = change["to"]
                    if fname in _DATE_FIELDS and isinstance(new_val, str):
                        new_val = datetime.date.fromisoformat(new_val)
                    setattr(task, fname, new_val)
                    update_fields.append(fname)

                task.save(update_fields=update_fields)
                updated += 1
                logger.info(
                    "schedule_writeback: updated task %s — fields %s", task.pk, update_fields
                )
            except Exception as exc:
                logger.warning(
                    "schedule_writeback: failed to apply proposal %s: %s", proposal.task_id, exc
                )
                errors.append(str(exc))

        return {"updated": updated, "errors": errors}

    # ── Private helpers ───────────────────────────────────────────────

    def _compute_changes(self, task, slots: dict, kind: str) -> dict:
        if kind == "UPDATE_DATE":
            return self._compute_date_changes(task, slots)
        if kind == "UPDATE_STATUS":
            return self._compute_status_changes(task, slots)
        if kind == "UPDATE_DURATION":
            return self._compute_duration_changes(task, slots)
        return {}

    def _compute_date_changes(self, task, slots: dict) -> dict:
        field_map = {
            "start_date": task.start_date,
            "end_date": task.end_date,
            "actual_start": task.actual_start,
            "actual_end": task.actual_end,
        }
        slot_field = slots.get("field", "end_date")
        target_fields = ["start_date", "end_date"] if slot_field == "both_dates" else [slot_field]

        changes: dict = {}
        for fname in target_fields:
            if fname not in field_map:
                continue
            current = field_map[fname]
            if slots.get("type") == "delta":
                delta = int(slots.get("delta_days") or 0)
                if current:
                    new_val = current + datetime.timedelta(days=delta)
                    changes[fname] = {"from": str(current), "to": str(new_val)}
            elif slots.get("type") == "absolute":
                abs_date = slots.get("absolute_date")
                if abs_date:
                    changes[fname] = {"from": str(current), "to": abs_date}
        return changes

    def _compute_status_changes(self, task, slots: dict) -> dict:
        new_status = (slots.get("status") or "").strip().lower()
        if not new_status or new_status not in _VALID_STATUSES or task.status == new_status:
            return {}
        return {"status": {"from": task.status, "to": new_status}}

    def _compute_duration_changes(self, task, slots: dict) -> dict:
        delta = int(slots.get("delta_days") or 0)
        if not delta or not task.end_date:
            return {}
        new_end = task.end_date + datetime.timedelta(days=delta)
        return {"end_date": {"from": str(task.end_date), "to": str(new_end)}}
