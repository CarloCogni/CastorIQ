# users/signals.py
"""
Signal handlers for the users app.

Single concern: keep every freshly created `User` in lock-step with the
auto-provisioned Sample Project so newcomers land on a working workspace
no matter how the row was created (beta approval, Django admin "Add User",
`createsuperuser`, or anything we add later).

Provisioning is split in two so user creation never blocks on the heavy
pipeline:

  * Fast, in-request: `provision_sample_project --skip-pipeline` creates the
    Project, copies the fixture files, and bootstraps membership. Sub-second,
    leaves the IFC/Document rows PENDING.
  * Slow, off-request: `run_sample_pipeline` parses the IFC models and embeds
    everything via Ollama — minutes of work. Running it inside the request that
    created the user blocked past nginx's `proxy_read_timeout` and returned a
    504 (e.g. on the admin "Add User" form). It is handed to a daemon thread so
    the request returns immediately; the Sample Project's entities/embeddings
    fill in shortly after.

Both run from `transaction.on_commit`, so the User row (and the Project rows the
fast step creates) are committed before the background thread reads them. If the
thread dies (container restart, crash) the files simply stay PENDING — recover
with `manage.py run_sample_pipeline <user_id>`.

Gated by `settings.PROVISION_SAMPLE_PROJECT_ON_USER_CREATE` so the test
suite can flip it off without touching individual factory call sites.
"""

from __future__ import annotations

import logging
import threading

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection, transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

logger = logging.getLogger(__name__)
User = get_user_model()


def _run_pipeline_in_thread(user_pk: str) -> None:
    """Run the slow sample-project pipeline off the request thread.

    Owns a fresh DB connection (one is created lazily on first query in this
    thread); close it on the way out so the thread doesn't leak a connection.
    Exceptions are logged, never re-raised — the rows are already committed, so
    a failure here just leaves files PENDING for a later `run_sample_pipeline`.
    """
    try:
        call_command("run_sample_pipeline", user_pk, verbosity=0)
    except Exception as exc:
        logger.exception("Deferred sample-project pipeline failed for user %s: %s", user_pk, exc)
    finally:
        connection.close()


def _provision_and_defer(user_pk: str) -> None:
    """Fast in-request provisioning, then hand the slow pipeline to a thread."""
    try:
        call_command("provision_sample_project", user_pk, skip_pipeline=True, verbosity=0)
    except Exception as exc:
        # Provisioning failure must NOT roll back account creation — the user
        # row is already committed by the time this runs. Log and bail; the
        # operator can re-run provision_sample_project to recover.
        logger.exception(
            "Fast sample-project provisioning failed in signal for user %s: %s", user_pk, exc
        )
        return

    thread = threading.Thread(
        target=_run_pipeline_in_thread,
        args=(user_pk,),
        name=f"sample-pipeline-{user_pk}",
        daemon=True,
    )
    thread.start()


@receiver(post_save, sender=User)
def provision_sample_project_on_user_create(sender, instance, created, **kwargs) -> None:
    """Provision the Sample Project for every newly created user.

    Fires on:
      - Beta approval (`beta/admin.py::approve_and_invite`)
      - Django admin "Add User" form
      - `manage.py createsuperuser`
      - Any future signup path

    No-op when:
      - `created` is False (only fire once, on insert)
      - `settings.PROVISION_SAMPLE_PROJECT_ON_USER_CREATE` is False (tests)
      - The user already has a Sample Project (the command itself short-circuits)
    """
    if not created:
        return
    if not getattr(settings, "PROVISION_SAMPLE_PROJECT_ON_USER_CREATE", True):
        return

    # Defer until the User row is committed so the background thread can read it.
    transaction.on_commit(lambda: _provision_and_defer(str(instance.pk)))
