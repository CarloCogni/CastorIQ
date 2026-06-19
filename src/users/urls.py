# users/urls.py
"""URLs for the Castor-branded account flow.

Covers first-time password set (welcome email) and forgot-password
recovery. Both flows render the same confirm + complete pages, but use
separate confirm URLs so each can carry its own token lifetime:
``invite/`` (30-day INVITE_LINK_TIMEOUT) vs ``set-password/`` (short
PASSWORD_RESET_TIMEOUT).
"""

from django.urls import path

from .views import (
    CastorPasswordResetDoneView,
    CastorPasswordResetView,
    InviteSetPasswordConfirmView,
    SetPasswordCompleteView,
    SetPasswordConfirmView,
)

urlpatterns = [
    path(
        "set-password/<uidb64>/<token>/",
        SetPasswordConfirmView.as_view(),
        name="users_set_password_confirm",
    ),
    # Beta welcome / invite landing — same page, but invite-lifetime tokens
    # (INVITE_LINK_TIMEOUT). Kept on its own URL so the forgot-password flow
    # above can keep the short default-token lifetime.
    path(
        "invite/<uidb64>/<token>/",
        InviteSetPasswordConfirmView.as_view(),
        name="users_invite_confirm",
    ),
    path(
        "set-password/done/",
        SetPasswordCompleteView.as_view(),
        name="users_set_password_complete",
    ),
    path(
        "reset-password/",
        CastorPasswordResetView.as_view(),
        name="users_password_reset",
    ),
    path(
        "reset-password/done/",
        CastorPasswordResetDoneView.as_view(),
        name="users_password_reset_done",
    ),
]
