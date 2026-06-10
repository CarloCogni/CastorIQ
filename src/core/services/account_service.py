# core/services/account_service.py
"""Self-service account deletion.

The user-facing button at ``Settings → Danger zone`` calls into this module
to hard-delete the requesting user. Django's existing CASCADE on
``Project.owner`` does the heavy lifting — owned projects, IFC files, Git
repos, memberships, chat sessions, modification proposals, BYOK keys, and
facility assets all disappear with the user row. SET_NULL audit tables
(``LLMCallLog``, ``ErrorLog``, ``GitCommit.author``, writeback review fields)
keep their rows with ``user=NULL`` so the operator still has an audit trail
of what happened in the system.
"""

import logging

from django.contrib.auth import get_user_model

logger = logging.getLogger(__name__)

User = get_user_model()


def impact_snapshot(user) -> dict[str, int]:
    """Count what a delete of ``user`` is about to cascade.

    Used both for the pre-delete audit log and for the confirmation modal so
    the user sees their real blast radius before confirming. Lazy-imports
    the cross-app models to keep this module light at import time.
    """
    from chat.models import ChatSession
    from environments.models import Project
    from ifc_processor.models import IFCFile
    from writeback.models import ModificationProposal

    return {
        "projects": Project.objects.filter(owner=user).count(),
        "ifc_files": IFCFile.objects.filter(project__owner=user).count(),
        "chat_sessions": ChatSession.objects.filter(user=user).count(),
        "proposals": ModificationProposal.objects.filter(created_by=user).count(),
    }


def delete_user_account(user) -> dict[str, int]:
    """Hard-delete ``user`` and everything their FK graph cascades to.

    Emits a warning-level audit log line *before* the row disappears so the
    operator can reconstruct what was wiped from logs alone. Returns the
    impact snapshot for callers that want to surface it (tests, future
    confirmation page).
    """
    impact = impact_snapshot(user)
    logger.warning(
        "account: deleting user %s (email=%s, joined=%s) — cascade impact: %s",
        user.username,
        user.email,
        user.date_joined.isoformat(),
        impact,
    )
    user.delete()
    return impact
