# writeback/migrations/0010_supersede_pre_journal_proposals.py
"""Retire proposals created before the mutation-journal cutover.

Every tier now writes through the MutationJournal and the legacy per-tier
executors are gone. A proposal created on the old path carries no
``schema_version`` key in ``changes``, so nothing can execute it — approving
one would only produce a confusing failure. Marking them superseded gives
the user the same signal they already understand from re-asking mid-review.

Forward is idempotent (re-running matches nothing). Reverse is a no-op: the
executor that could have run these no longer exists, so restoring the
statuses would only recreate un-executable work.
"""

from django.db import migrations


def supersede_pre_journal_proposals(apps, schema_editor):
    """Mark actionable non-journal proposals as superseded."""
    ModificationProposal = apps.get_model("writeback", "ModificationProposal")

    actionable = ModificationProposal.objects.filter(status__in=["pending", "approved"])
    # `changes` is a JSONField; has_key works on dict payloads and simply
    # excludes rows whose payload is a list or null.
    stale_ids = list(
        actionable.exclude(changes__has_key="schema_version").values_list("id", flat=True)
    )
    if not stale_ids:
        return

    ModificationProposal.objects.filter(id__in=stale_ids).update(status="superseded")


def noop_reverse(apps, schema_editor):
    """Nothing to restore — the legacy executor is gone."""


class Migration(migrations.Migration):
    dependencies = [
        ("writeback", "0009_modificationproposal_code_review_acknowledged_at_and_more"),
    ]

    operations = [
        migrations.RunPython(supersede_pre_journal_proposals, noop_reverse),
    ]
