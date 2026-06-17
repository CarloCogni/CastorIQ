# users/urls.py
"""URLs for the Castor-branded account flow.

Covers first-time password set (welcome email) and forgot-password
recovery. Both flows share the ``set-password/`` confirm + complete
pages — only the entry point differs.
"""

from django.urls import path

from .views import (
    CastorPasswordResetDoneView,
    CastorPasswordResetView,
    SetPasswordCompleteView,
    SetPasswordConfirmView,
)

urlpatterns = [
    path(
        "set-password/<uidb64>/<token>/",
        SetPasswordConfirmView.as_view(),
        name="users_set_password_confirm",
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
