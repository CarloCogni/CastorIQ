# users/tokens.py
"""Token machinery for the account flow.

The beta welcome email's set-password link reuses Django's password-reset
token, but it must outlive a forgot-password recovery link by a wide margin:
the operator approves an applicant, who then registers whenever they next open
their email — possibly days later. Django's ``PasswordResetTokenGenerator``
hard-codes ``settings.PASSWORD_RESET_TIMEOUT`` in ``check_token``, so the only
way to give invite links a separate lifetime is to subclass and swap that one
setting. Everything else (HMAC, secret rotation, timestamp encoding) is
inherited unchanged.
"""

import logging

from django.conf import settings
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.utils.crypto import constant_time_compare
from django.utils.http import base36_to_int

logger = logging.getLogger(__name__)


class InviteTokenGenerator(PasswordResetTokenGenerator):
    """Set-password token whose lifetime is ``INVITE_LINK_TIMEOUT``.

    Identical to ``PasswordResetTokenGenerator`` except the expiry check reads
    ``settings.INVITE_LINK_TIMEOUT`` instead of ``settings.PASSWORD_RESET_TIMEOUT``.
    """

    def check_token(self, user, token) -> bool:
        """Validate a token, bounding its age by ``INVITE_LINK_TIMEOUT``.

        Mirrors ``PasswordResetTokenGenerator.check_token`` (Django 5.2) line for
        line, swapping only the timeout setting. Re-check this body on a Django
        upgrade — it is the one place coupled to the parent's internals.
        """
        if not (user and token):
            return False
        # Parse the token
        try:
            ts_b36, _ = token.split("-")
        except ValueError:
            return False

        try:
            ts = base36_to_int(ts_b36)
        except ValueError:
            return False

        # Check that the timestamp/uid has not been tampered with
        for secret in [self.secret, *self.secret_fallbacks]:
            if constant_time_compare(
                self._make_token_with_timestamp(user, ts, secret),
                token,
            ):
                break
        else:
            return False

        # Check the timestamp is within limit — the ONLY divergence from the parent.
        if (self._num_seconds(self._now()) - ts) > settings.INVITE_LINK_TIMEOUT:
            return False

        return True


# Module-level singleton, mirroring django.contrib.auth.tokens.default_token_generator.
invite_token_generator = InviteTokenGenerator()


def humanize_timeout(seconds: int) -> str:
    """Render a timeout in seconds as a coarse human label for user-facing copy.

    Picks the largest whole unit (days → hours → minutes) so welcome emails and
    the expired-link page read "30 days" / "1 hour" / "15 minutes" straight from
    the configured setting — no hardcoded lifetime that can drift out of sync.
    """
    if seconds >= 86_400:
        days = seconds // 86_400
        return f"{days} day{'s' if days != 1 else ''}"
    if seconds >= 3_600:
        hours = seconds // 3_600
        return f"{hours} hour{'s' if hours != 1 else ''}"
    minutes = max(1, seconds // 60)
    return f"{minutes} minute{'s' if minutes != 1 else ''}"
