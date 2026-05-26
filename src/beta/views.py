# beta/views.py
"""Public-facing views for the beta funnel."""

import logging
import re
import time

from django.conf import settings
from django.contrib import messages
from django.core.mail import EmailMultiAlternatives, send_mail
from django.shortcuts import redirect
from django.template.loader import render_to_string
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit

from core.models import SiteLaunchConfig

from .models import BetaApplication
from .throttle import bump_daily_count, client_ip, ratelimit_ip_key, today_send_count

logger = logging.getLogger(__name__)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
THROTTLE_SECONDS = 60  # one submission per minute per session
SESSION_THROTTLE_KEY = "beta_apply_last_submit"


def _send_confirmation_email(application: BetaApplication) -> None:
    """Send a 'thanks, we'll review' confirmation to the applicant.

    Best-effort: an email backend misconfiguration must not lose the
    submission. We log and swallow. Skipped — without raising — when today's
    Brevo budget is near exhaustion (``BETA_DAILY_TOTAL_CAP``); the row still
    persists so the operator can follow up manually.
    """
    if today_send_count() >= settings.BETA_DAILY_TOTAL_CAP:
        logger.warning(
            "Skipping confirmation to %s — daily Brevo cap reached (%s)",
            application.email,
            settings.BETA_DAILY_TOTAL_CAP,
        )
        return
    try:
        ctx = {"application": application, "site_url": settings.SITE_URL}
        text_body = render_to_string("beta/email/application_received.txt", ctx)
        html_body = render_to_string("beta/email/application_received.html", ctx)
        msg = EmailMultiAlternatives(
            subject="We received your CastorIQ beta application",
            body=text_body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[application.email],
        )
        msg.attach_alternative(html_body, "text/html")
        msg.send(fail_silently=False)
        bump_daily_count()
    except Exception as exc:
        logger.warning("Could not send confirmation email to %s: %s", application.email, exc)


def _send_operator_notification(application: BetaApplication) -> None:
    """Ping the operator inbox so new applications get noticed without manual polling.

    Three short-circuits, in order:
      1. ``OPERATOR_NOTIFICATION_EMAIL`` unset (dev default).
      2. ``SiteLaunchConfig.notify_operator_on_application`` toggled off in admin.
      3. Today's send count past ``BETA_DAILY_OPERATOR_CAP`` (preserves the
         remaining Brevo budget for applicant confirmations and password
         resets).

    Best-effort otherwise — a failed operator ping must not affect the
    applicant flow.
    """
    operator = settings.OPERATOR_NOTIFICATION_EMAIL
    if not operator:
        return
    if not SiteLaunchConfig.load().notify_operator_on_application:
        return
    if today_send_count() >= settings.BETA_DAILY_OPERATOR_CAP:
        logger.warning(
            "Skipping operator notification for %s — operator cap reached (%s)",
            application.email,
            settings.BETA_DAILY_OPERATOR_CAP,
        )
        return
    try:
        admin_url = (
            f"{settings.SITE_URL.rstrip('/')}/admin/beta/betaapplication/{application.id}/change/"
        )
        body = (
            f"New Castor beta application.\n\n"
            f"Name:        {application.name}\n"
            f"Email:       {application.email}\n"
            f"Job title:   {application.job_title or '—'}\n"
            f"Submitted:   {application.created_at:%Y-%m-%d %H:%M %Z}\n\n"
            f"Why they're interested:\n{application.description}\n\n"
            f"Review in admin: {admin_url}\n"
        )
        send_mail(
            subject=f"New Castor beta application: {application.name} ({application.email})",
            message=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[operator],
            fail_silently=False,
        )
        bump_daily_count()
    except Exception as exc:
        logger.warning("Could not send operator notification for %s: %s", application.email, exc)


@require_POST
@ratelimit(
    key=ratelimit_ip_key,
    rate=settings.BETA_RATE_LIMIT,
    method="POST",
    group="beta_apply",
    block=False,
)
def apply_view(request):
    """Receive a beta application from the public landing form.

    Pipeline: honeypot → throttle → field validation → dedupe-by-email →
    persist → confirmation email → redirect back to /. All errors and the
    success message are surfaced via Django's messages framework, which the
    landing template renders.

    Short-circuited when the site is not in ``live`` state — during
    coming-soon or maintenance the form is unreachable from the splash, but
    we still reject direct POSTs so cached links / scripts can't create
    BetaApplication rows.
    """
    if not SiteLaunchConfig.load().is_live:
        return redirect("/")

    # django-ratelimit decorator already counted this hit; ``request.limited``
    # tells us whether the IP exceeded BETA_RATE_LIMIT. We bail with a friendly
    # toast rather than a 403 so the user sees the same UX as the session
    # throttle below.
    if getattr(request, "limited", False):
        logger.info("Beta apply rate-limited from %s", client_ip(request))
        messages.error(
            request,
            "Too many submissions from this network. Please try again in an hour.",
        )
        return redirect("/#apply")

    # Honeypot — bots fill any input they can see; humans don't see this one.
    if request.POST.get("company_website", "").strip():
        logger.info("Beta application honeypot triggered from %s", client_ip(request))
        # Pretend success so the bot doesn't learn anything from the response.
        messages.success(request, "Thanks — your application was received.")
        return redirect("/")

    # Per-session throttle. Real per-IP rate-limiting belongs at the proxy
    # layer; this just keeps a determined human from spamming the form.
    last = request.session.get(SESSION_THROTTLE_KEY)
    now = int(time.time())
    if last and now - last < THROTTLE_SECONDS:
        messages.error(
            request,
            f"Please wait {THROTTLE_SECONDS - (now - last)}s before submitting again.",
        )
        return redirect("/#apply")

    email = (request.POST.get("email") or "").strip().lower()
    name = (request.POST.get("name") or "").strip()
    job_title = (request.POST.get("job_title") or "").strip()
    description = (request.POST.get("description") or "").strip()

    if not email or not EMAIL_RE.match(email):
        messages.error(request, "A valid email is required.")
        return redirect("/#apply")
    if not name:
        messages.error(request, "A full name is required.")
        return redirect("/#apply")
    if not description:
        messages.error(request, "Please tell us why you're interested.")
        return redirect("/#apply")

    # Dedupe — if there's already an application for this email, surface a
    # friendly message rather than creating a duplicate row that will fail
    # the unique constraint anyway.
    existing = BetaApplication.objects.filter(email=email).first()
    if existing:
        if existing.status == BetaApplication.Status.APPROVED:
            messages.info(
                request,
                "An account is already approved for this email — check your inbox "
                "for the welcome message.",
            )
        elif existing.status == BetaApplication.Status.REJECTED:
            messages.info(
                request,
                "Your previous application was reviewed. Please email the operator "
                "directly if you'd like to reapply.",
            )
        else:
            messages.info(
                request,
                "We've already received an application for this email — sit tight, "
                "we'll be in touch.",
            )
        return redirect("/#apply")

    application = BetaApplication.objects.create(
        email=email,
        name=name,
        job_title=job_title,
        description=description,
        submitted_ip=client_ip(request) or None,
        submitted_user_agent=request.META.get("HTTP_USER_AGENT", "")[:500],
    )
    request.session[SESSION_THROTTLE_KEY] = now
    logger.info("Beta application received from %s (id=%s)", email, application.id)

    _send_confirmation_email(application)
    _send_operator_notification(application)
    messages.success(
        request,
        "Thanks! We received your application and will review it shortly.",
    )
    return redirect("/#apply")
