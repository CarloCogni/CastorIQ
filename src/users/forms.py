# users/forms.py
"""User-facing forms for the Castor account flow."""

from django.contrib.auth import get_user_model
from django.contrib.auth.forms import PasswordResetForm

UserModel = get_user_model()


class CastorPasswordResetForm(PasswordResetForm):
    """Password-reset form that includes users with unusable passwords.

    Django's stock ``PasswordResetForm.get_users()`` excludes any user
    whose ``has_usable_password()`` is ``False``. Beta users approved via
    ``beta.admin.approve_and_invite`` have ``set_unusable_password()``
    called and stay in that state until they complete first-time
    set-password from the welcome email. Without this override, a beta
    user who loses the welcome email is silently invisible to the reset
    form and locked out of recovery.

    Security is preserved: the reset token is still email-bound, signed,
    short-lived (``settings.PASSWORD_RESET_TIMEOUT``, 7 days by default),
    and single-use.
    """

    def get_users(self, email):
        email_field = UserModel.get_email_field_name()
        active_users = UserModel._default_manager.filter(
            **{f"{email_field}__iexact": email, "is_active": True}
        )
        return (u for u in active_users)
