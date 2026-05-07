# core/auth_backends.py
"""Authentication backends for Castor.

``EmailOrUsernameModelBackend`` lets a user sign in with either their username
or their email address. With ``users.User`` enforcing ``unique=True`` on email,
the email lookup can never produce ``MultipleObjectsReturned`` — but we still
defend against it (e.g. someone running raw SQL) with the same generic-fail
treatment as wrong password.

Anti-enumeration: when the lookup misses, run ``UserModel().set_password()`` so
wall-clock time matches a real check. This is the same trick Django's stock
``ModelBackend`` uses for unknown usernames.
"""

import logging

from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend

logger = logging.getLogger(__name__)


class EmailOrUsernameModelBackend(ModelBackend):
    """Authenticate against either ``User.username`` or ``User.email``."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        if not username or not password:
            return None

        UserModel = get_user_model()  # noqa: N806

        # Try username first (case-sensitive — Django default).
        try:
            user = UserModel._default_manager.get_by_natural_key(username)
        except UserModel.DoesNotExist:
            user = self._lookup_by_email(UserModel, username)

        if user is None:
            # Run a hash to keep wrong-username and wrong-password isochronous.
            UserModel().set_password(password)
            return None

        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None

    @staticmethod
    def _lookup_by_email(UserModel, value):  # noqa: N803
        """Return the user with this email or ``None``. Email match is case-insensitive."""
        try:
            return UserModel._default_manager.get(email__iexact=value)
        except UserModel.DoesNotExist:
            return None
        except UserModel.MultipleObjectsReturned:
            # Should never fire under the unique constraint; if it does, fail
            # closed and log loudly so the operator notices.
            logger.error("EmailOrUsernameModelBackend: multiple users share email %r", value)
            return None
