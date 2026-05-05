# beta/views.py
"""Public-facing views for the beta funnel."""

import logging
import re
import time

from django.conf import settings
from django.contrib import messages
from django.core.mail import EmailMultiAlternatives
from django.shortcuts import redirect
from django.template.loader import render_to_string
from django.views.decorators.http import require_POST

from .models import BetaApplication

logger = logging.getLogger(__name__)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
THROTTLE_SECONDS = 60  # one submission per minute per session
SESSION_THROTTLE_KEY = "beta_apply_last_submit"


def _client_ip(request) -> str:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


def _send_confirmation_email(application: BetaApplication) -> None:
    """Send a 'thanks, we'll review' confirmation to the applicant.

    Best-effort: an email backend misconfiguration must not lose the
    submission. We log and swallow.
    """
    try:
        ctx = {"application": application, "site_url": settings.SITE_URL}
        text_body = render_to_string("beta/email/application_received.txt", ctx)
        html_body = render_to_string("beta/email/application_received.html", ctx)
        msg = EmailMultiAlternatives(
            subject="We received your Castor beta application",
            body=text_body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[application.email],
        )
        msg.attach_alternative(html_body, "text/html")
        msg.send(fail_silently=False)
    except Exception as exc:
        logger.warning("Could not send confirmation email to %s: %s", application.email, exc)


@require_POST
def apply_view(request):
    """Receive a beta application from the public landing form.

    Pipeline: honeypot → throttle → field validation → dedupe-by-email →
    persist → confirmation email → redirect back to /. All errors and the
    success message are surfaced via Django's messages framework, which the
    landing template renders.
    """
    # Honeypot — bots fill any input they can see; humans don't see this one.
    if request.POST.get("company_website", "").strip():
        logger.info("Beta application honeypot triggered from %s", _client_ip(request))
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
        submitted_ip=_client_ip(request) or None,
        submitted_user_agent=request.META.get("HTTP_USER_AGENT", "")[:500],
    )
    request.session[SESSION_THROTTLE_KEY] = now
    logger.info("Beta application received from %s (id=%s)", email, application.id)

    _send_confirmation_email(application)
    messages.success(
        request,
        "Thanks! We received your application and will review it shortly.",
    )
    return redirect("/#apply")
