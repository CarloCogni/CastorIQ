# core/services/auth_service.py
"""Two-step login flow service.

Step 1 collects a username and stages it in the session under a single dict.
Step 2 reads it back, calls ``django.contrib.auth.authenticate``, and either
logs the user in or returns ``None``. Anti-enumeration is intrinsic: step 1
never touches the database, and Django's ``ModelBackend`` runs a dummy
``set_password`` pass for unknown users so the wall-clock cost of a wrong
password matches that of an unknown one.
"""

import logging
import time

from django.contrib.auth import authenticate, login
from django.contrib.auth.base_user import AbstractBaseUser
from django.http import HttpRequest

logger = logging.getLogger(__name__)

# Session key for the staged username + when it was staged.
_SESSION_KEY = "_login_step"

# How long a staged step-1 stays valid. Anything longer is just stale state
# pretending to be useful — the user has already moved on or closed the tab.
_STAGE_TTL_SECONDS = 600  # 10 minutes


def stage_login_attempt(request: HttpRequest, username: str) -> None:
    """Persist the claimed username on ``request.session`` for step 2.

    No DB read here — staging an unknown username is indistinguishable from
    staging a known one, which is exactly what anti-enumeration needs.
    """
    request.session[_SESSION_KEY] = {
        "username": username,
        "staged_at": int(time.time()),
    }
    request.session.modified = True


def get_staged_username(request: HttpRequest) -> str | None:
    """Return the staged username if step 1 has been completed and is fresh."""
    payload = request.session.get(_SESSION_KEY)
    if not payload:
        return None
    if int(time.time()) - int(payload.get("staged_at", 0)) > _STAGE_TTL_SECONDS:
        clear_login_stage(request)
        return None
    return payload.get("username") or None


def clear_login_stage(request: HttpRequest) -> None:
    """Drop any staged step-1 state. Called after success or on reset."""
    if _SESSION_KEY in request.session:
        del request.session[_SESSION_KEY]
        request.session.modified = True


def complete_login_attempt(request: HttpRequest, password: str) -> AbstractBaseUser | None:
    """Authenticate the staged username with ``password``.

    Returns the authenticated ``User`` on success and logs them in. Returns
    ``None`` for any failure (no staged username, expired stage, unknown user,
    wrong password, inactive account) — callers MUST surface a single generic
    error, never a reason.
    """
    username = get_staged_username(request)
    if not username:
        return None

    user = authenticate(request, username=username, password=password)
    if user is None:
        logger.info("login: authentication failed for staged user")
        return None

    login(request, user)
    clear_login_stage(request)
    logger.info("login: %s authenticated", user.username)
    return user
