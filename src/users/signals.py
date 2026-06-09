# users/signals.py
"""
Signal handlers for the users app.

Single concern: keep every freshly created `User` in lock-step with the
auto-provisioned Sample Project so newcomers land on a working workspace
no matter how the row was created (beta approval, Django admin "Add User",
`createsuperuser`, or anything we add later).

Gated by `settings.PROVISION_SAMPLE_PROJECT_ON_USER_CREATE` so the test
suite can flip it off without touching individual factory call sites.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db.models.signals import post_save
from django.dispatch import receiver

logger = logging.getLogger(__name__)
User = get_user_model()


@receiver(post_save, sender=User)
def provision_sample_project_on_user_create(sender, instance, created, **kwargs):
    """Run `provision_sample_project` for every newly created user.

    Fires on:
      - Beta approval (`beta/admin.py::approve_and_invite`)
      - Django admin "Add User" form
      - `manage.py createsuperuser`
      - Any future signup path

    No-op when:
      - `created` is False (only fire once, on insert)
      - `settings.PROVISION_SAMPLE_PROJECT_ON_USER_CREATE` is False (tests)
      - The user already has a Sample Project (the command itself short-circuits)

    Exceptions are logged but never re-raised: the User row is already
    saved by the time post_save fires, and provisioning failure must NOT
    roll back the account creation. The beta flow's explicit
    `call_command` in `beta/admin.py` is the path that captures
    failures into `BetaApplication.provisioning_error` for the operator
    to see in the admin list view.
    """
    if not created:
        return
    if not getattr(settings, "PROVISION_SAMPLE_PROJECT_ON_USER_CREATE", True):
        return

    try:
        call_command("provision_sample_project", str(instance.pk), verbosity=0)
    except Exception as exc:
        logger.exception(
            "Sample-project provisioning failed in post_save signal for user %s: %s",
            instance.username,
            exc,
        )
