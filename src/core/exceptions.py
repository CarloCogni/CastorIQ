"""Custom exception handlers and error logging utilities"""

import logging

from django.http import HttpRequest

from core.utils import get_full_stack  # Import the magic function

logger = logging.getLogger(__name__)


def log_exception(
    exception: Exception,
    request: HttpRequest | None = None,
    severity: str = "error",
    extra_context: dict | None = None,
) -> None:
    """
    Log an exception to the database with FULL context including complete stack trace.

    Args:
        exception: The exception instance
        request: Optional Django request object
        severity: Error severity level (debug, info, warning, error, critical)
        extra_context: Additional context to store
    """
    # Import here to avoid circular imports
    from core.models import ErrorLog

    try:
        # Get COMPLETE stack trace using our utility function
        stacktrace = get_full_stack()

        # Prepare error data
        error_data = {
            "severity": severity,
            "message": str(exception),
            "exception_type": exception.__class__.__name__,
            "stacktrace": stacktrace,
        }

        # Add request context if available
        if request:
            error_data.update(
                {
                    "url": request.build_absolute_uri(),
                    "method": request.method,
                    "user": request.user if request.user.is_authenticated else None,
                    "user_agent": request.META.get("HTTP_USER_AGENT", ""),
                    "ip_address": get_client_ip(request),
                    "view_name": request.resolver_match.view_name if request.resolver_match else "",
                }
            )

            # Collect request data (filter sensitive info)
            request_data = {
                "GET": dict(request.GET),
                "POST": _filter_sensitive_data(dict(request.POST)),
            }

            # Add extra context if provided
            if extra_context:
                request_data["extra"] = extra_context

            error_data["request_data"] = request_data

        # Save to database
        ErrorLog.objects.create(**error_data)

    except Exception as e:
        # If error logging fails, at least log to console
        logger.exception(f"Failed to log error to database: {e}")


def get_client_ip(request: HttpRequest) -> str | None:
    """Extract client IP from request, handling proxies"""
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded_for:
        ip = x_forwarded_for.split(",")[0]
    else:
        ip = request.META.get("REMOTE_ADDR")
    return ip


def _filter_sensitive_data(data: dict) -> dict:
    """Remove sensitive fields from request data"""
    sensitive_keys = {
        "password",
        "password1",
        "password2",
        "old_password",
        "new_password",
        "confirm_password",
        "csrfmiddlewaretoken",
        "csrf_token",
        "api_key",
        "secret",
        "token",
    }

    filtered = {}
    for key, value in data.items():
        if key.lower() in sensitive_keys:
            filtered[key] = "***FILTERED***"
        else:
            filtered[key] = value

    return filtered
