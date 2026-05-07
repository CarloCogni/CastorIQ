# core/forms.py
"""Forms for the public unauthenticated surfaces.

Login is split into two steps. Each form is single-field on purpose: step 1
captures only the username (which is the email for beta users), step 2 only
the password. The view orchestrates the transition; the forms validate.
"""

from django import forms


class LoginUsernameForm(forms.Form):
    """Step 1 of the two-step login.

    Accepts either a username or an email — the backend
    (``core.auth_backends.EmailOrUsernameModelBackend``) tries username first
    and falls back to email lookup. The field is still ``username`` so password
    managers fill it via the ``autocomplete="username"`` standard.
    """

    username = forms.CharField(
        max_length=254,  # email max
        strip=True,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "username or you@example.com",
                "autocomplete": "username",
                "autofocus": "autofocus",
                "required": "required",
            }
        ),
    )


class LoginPasswordForm(forms.Form):
    """Step 2 of the two-step login — password only."""

    password = forms.CharField(
        widget=forms.PasswordInput(
            attrs={
                "class": "form-control",
                "placeholder": "••••••••",
                "autocomplete": "current-password",
                "autofocus": "autofocus",
                "required": "required",
            }
        ),
    )
