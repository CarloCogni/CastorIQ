# beta/admin.py
"""Operator-facing admin for the beta vetting funnel."""

import logging

from django.conf import settings
from django.contrib import admin, messages
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from .models import BetaApplication

logger = logging.getLogger(__name__)
User = get_user_model()


def _build_set_password_url(user) -> str:
    """Build an absolute URL to Django's PasswordResetConfirmView for a user.

    Reuses the same token machinery the standard "Forgot password?" flow
    uses, so the link expires per ``settings.PASSWORD_RESET_TIMEOUT`` (7 days
    by default, set in M3.2) and is single-use.
    """
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    path = reverse("password_reset_confirm", kwargs={"uidb64": uid, "token": token})
    return settings.SITE_URL.rstrip("/") + path


def _send_welcome_email(application: BetaApplication, set_password_url: str) -> None:
    ctx = {
        "user": application.created_user,
        "user_full_name": application.name,
        "set_password_url": set_password_url,
        "site_url": settings.SITE_URL,
    }
    text_body = render_to_string("beta/email/welcome.txt", ctx)
    html_body = render_to_string("beta/email/welcome.html", ctx)
    msg = EmailMultiAlternatives(
        subject="Welcome to Castor — set your password",
        body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[application.email],
    )
    msg.attach_alternative(html_body, "text/html")
    msg.send(fail_silently=False)


@admin.register(BetaApplication)
class BetaApplicationAdmin(admin.ModelAdmin):
    list_display = (
        "email",
        "name",
        "job_title",
        "status",
        "needs_provisioning_repair",
        "created_at",
        "reviewed_by",
        "reviewed_at",
    )
    list_filter = ("status", "created_at")
    search_fields = ("email", "name", "job_title", "description", "notes")

    @admin.display(boolean=True, description="Provisioning OK?")
    def needs_provisioning_repair(self, obj: BetaApplication) -> bool:
        # Inverted: True = healthy (no provisioning error recorded).
        return not obj.provisioning_error

    readonly_fields = (
        "id",
        "submitted_ip",
        "submitted_user_agent",
        "created_user",
        "created_at",
        "updated_at",
    )
    list_per_page = 50
    date_hierarchy = "created_at"

    fieldsets = (
        ("Application", {"fields": ("email", "name", "job_title", "description")}),
        (
            "Vetting",
            {
                "fields": (
                    "status",
                    "notes",
                    "reviewed_by",
                    "reviewed_at",
                    "created_user",
                    "provisioning_error",
                ),
            },
        ),
        (
            "Submission context",
            {
                "fields": ("submitted_ip", "submitted_user_agent", "created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
        ("Internal", {"fields": ("id",), "classes": ("collapse",)}),
    )

    actions = ["approve_and_invite", "mark_called", "mark_rejected"]

    @admin.action(description="Approve → create User + send welcome email")
    def approve_and_invite(self, request, queryset):
        """Approve selected applications: create User, email set-password link.

        Idempotent: re-running on an already-approved application skips it
        rather than creating a duplicate User. Errors per row are surfaced
        as warning messages and don't abort the batch.

        Each new user also gets a UserTokenBudget row implicitly via the
        ``token_budget`` context processor on first request, so no setup
        is required here. Sample-project provisioning lands in M4.
        """
        approved = 0
        skipped = 0
        for application in queryset:
            try:
                if application.status == BetaApplication.Status.APPROVED:
                    skipped += 1
                    continue

                # Create or reuse the User. Username = email so the login form
                # accepts the same string the applicant typed.
                user, created = User.objects.get_or_create(
                    username=application.email,
                    defaults={
                        "email": application.email,
                        "first_name": application.name.split()[0] if application.name else "",
                        "last_name": " ".join(application.name.split()[1:])
                        if application.name and len(application.name.split()) > 1
                        else "",
                        "is_active": True,
                    },
                )
                if created:
                    user.set_unusable_password()  # forces the welcome flow
                    user.save()

                # Sample-project provisioning. The post_save signal in
                # users/signals.py already runs the same command during
                # `get_or_create` above, so this explicit call is usually
                # a no-op (idempotency short-circuits). It stays in place
                # to capture failures into BetaApplication.provisioning_error
                # — the signal swallows exceptions, this branch records them.
                provisioning_error = ""
                try:
                    from django.core.management import call_command

                    call_command(
                        "provision_sample_project",
                        str(user.pk),
                        verbosity=0,
                    )
                except Exception as prov_exc:
                    logger.exception(
                        "Sample-project provisioning failed for %s: %s",
                        application.email,
                        prov_exc,
                    )
                    provisioning_error = f"{type(prov_exc).__name__}: {prov_exc}"
                    self.message_user(
                        request,
                        f"{application.email}: account created and email queued, "
                        f"but sample-project provisioning failed ({prov_exc}). "
                        "Re-run `manage.py provision_sample_project --force-files` to recover.",
                        level=messages.WARNING,
                    )

                set_password_url = _build_set_password_url(user)
                _send_welcome_email(application, set_password_url)

                application.status = BetaApplication.Status.APPROVED
                application.reviewed_by = request.user
                application.reviewed_at = timezone.now()
                application.created_user = user
                application.provisioning_error = provisioning_error
                application.save(
                    update_fields=[
                        "status",
                        "reviewed_by",
                        "reviewed_at",
                        "created_user",
                        "provisioning_error",
                    ]
                )
                approved += 1
                logger.info(
                    "Beta application approved: email=%s user=%s reviewer=%s",
                    application.email,
                    user.username,
                    request.user.username,
                )
            except Exception as exc:
                logger.exception("Approval failed for application %s: %s", application.id, exc)
                self.message_user(
                    request,
                    f"Could not approve {application.email}: {exc}",
                    level=messages.WARNING,
                )

        if approved:
            self.message_user(
                request,
                f"Approved {approved} application(s); welcome emails sent.",
                level=messages.SUCCESS,
            )
        if skipped:
            self.message_user(
                request,
                f"Skipped {skipped} already-approved application(s).",
                level=messages.INFO,
            )

    @admin.action(description="Mark as 'intro call scheduled'")
    def mark_called(self, request, queryset):
        updated = queryset.update(
            status=BetaApplication.Status.CALLED,
            reviewed_by=request.user,
            reviewed_at=timezone.now(),
        )
        self.message_user(request, f"Marked {updated} application(s) as called.")

    @admin.action(description="Mark as rejected")
    def mark_rejected(self, request, queryset):
        updated = queryset.update(
            status=BetaApplication.Status.REJECTED,
            reviewed_by=request.user,
            reviewed_at=timezone.now(),
        )
        self.message_user(request, f"Rejected {updated} application(s).")
