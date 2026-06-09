# users/models.py
"""Custom user model for Castor.

We swap to a custom user model on day 0 so we can enforce ``email`` as both
required and unique — Django's default ``auth.User`` allows blank, non-unique
emails which would foreclose email-as-login. The custom model also gives us a
clean place to add future per-user fields without another migration scramble.

Everything else inherits from ``AbstractUser`` so admin, password reset, and
all third-party integrations keep working unchanged.
"""

from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models


class User(AbstractUser):
    """Castor user. Same shape as ``auth.User`` but with a unique, required email."""

    email = models.EmailField(
        verbose_name="email address",
        unique=True,
        blank=False,
    )

    # ``createsuperuser`` must prompt for email so the unique constraint never
    # gets violated by a CLI-created superuser sharing "" with a future signup.
    REQUIRED_FIELDS = ["email"]

    class Meta:
        verbose_name = "user"
        verbose_name_plural = "users"

    def __str__(self) -> str:
        return self.username

    def clean(self) -> None:
        # `blank=False` only fires through form-level validation, which a stale
        # admin form or a direct ORM call can skip. Reject empty/whitespace here
        # so any path that runs `full_clean()` gets a readable ValidationError
        # instead of a downstream UniqueViolation on the empty-email slot.
        super().clean()
        if not (self.email or "").strip():
            raise ValidationError({"email": "Email is required."})
        self.email = self.email.strip().lower()
