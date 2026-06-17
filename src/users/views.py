# users/views.py
"""Castor-branded views for the account flow.

Three pairs of subclasses, sharing the second half:

- First-time password set (welcome email): ``SetPasswordConfirmView`` +
  ``SetPasswordCompleteView``.
- Forgot password (login link): ``CastorPasswordResetView`` +
  ``CastorPasswordResetDoneView`` → the email link lands the user on
  ``SetPasswordConfirmView`` (reused) → ``SetPasswordCompleteView``.

Django's stock ``password_reset_*`` URLs in ``config/urls.py`` stay
in place — admin still uses them internally — but no user-facing
surface routes to them anymore.
"""

from django.contrib.auth import views as auth_views
from django.urls import reverse_lazy

from .forms import CastorPasswordResetForm


class SetPasswordConfirmView(auth_views.PasswordResetConfirmView):
    """Branded set-password page reached from welcome and reset emails."""

    template_name = "users/set_password.html"
    success_url = reverse_lazy("users_set_password_complete")


class SetPasswordCompleteView(auth_views.PasswordResetCompleteView):
    """Branded confirmation page shown after a password is set."""

    template_name = "users/set_password_complete.html"


class CastorPasswordResetView(auth_views.PasswordResetView):
    """Branded "Forgot password?" entry — emails a reset link."""

    template_name = "users/password_reset.html"
    email_template_name = "users/email/password_reset_email.txt"
    html_email_template_name = "users/email/password_reset_email.html"
    subject_template_name = "users/email/password_reset_subject.txt"
    form_class = CastorPasswordResetForm
    success_url = reverse_lazy("users_password_reset_done")


class CastorPasswordResetDoneView(auth_views.PasswordResetDoneView):
    """Branded "Check your email" confirmation."""

    template_name = "users/password_reset_done.html"
