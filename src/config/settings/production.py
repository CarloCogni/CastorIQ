"""Production settings."""

import os

from .base import *  # noqa: F401, F403

DEBUG = False

# Sentry — error visibility in production. Initialised here only (never in
# dev/local) so the Sentry SDK doesn't try to phone home from a developer
# machine. SENTRY_DSN being unset is a normal state — Sentry account creation
# is the operator's job, not the deploy script's.
_sentry_dsn = os.getenv("SENTRY_DSN", "").strip()
if _sentry_dsn:
    import sentry_sdk
    from sentry_sdk.integrations.django import DjangoIntegration
    from sentry_sdk.integrations.logging import LoggingIntegration

    sentry_sdk.init(
        dsn=_sentry_dsn,
        integrations=[
            DjangoIntegration(),
            # WARNING-and-above goes as a breadcrumb, ERROR-and-above as an event.
            LoggingIntegration(level=None, event_level=None),
        ],
        environment=os.getenv("SENTRY_ENVIRONMENT", "production"),
        release=os.getenv("SENTRY_RELEASE", ""),
        traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.0")),
        send_default_pii=False,
    )

# Security settings
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"

# HTTPS — production lives behind nginx with Let's Encrypt; the proxy speaks
# HTTP to Daphne, so SECURE_PROXY_SSL_HEADER is required for Django to know
# the request was originally HTTPS. Without it, secure-cookie + redirect logic
# loops forever.
SECURE_SSL_REDIRECT = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_HSTS_SECONDS = int(os.getenv("SECURE_HSTS_SECONDS", str(60 * 60 * 24 * 365)))
SECURE_HSTS_PRELOAD = True
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# Allowed hosts and CSRF origins from env so we don't bake the domain into code.
ALLOWED_HOSTS = [h.strip() for h in os.getenv("DJANGO_ALLOWED_HOSTS", "").split(",") if h.strip()]
CSRF_TRUSTED_ORIGINS = [
    o.strip() for o in os.getenv("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()
]

# Email — base.py reads EMAIL_BACKEND from env, but in production we expect
# real SMTP. Override the default explicitly so a missing env var fails loud
# (no silent console-fallback in prod).
EMAIL_BACKEND = os.getenv("EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend")

# Database from environment
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("POSTGRES_DB", "castor"),
        "USER": os.getenv("POSTGRES_USER", "castor"),
        "PASSWORD": os.getenv("POSTGRES_PASSWORD"),
        "HOST": os.getenv("POSTGRES_HOST", "db"),
        "PORT": os.getenv("POSTGRES_PORT", "5432"),
    }
}
