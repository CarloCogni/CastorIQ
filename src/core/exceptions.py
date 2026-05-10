"""Custom exception handlers and error logging utilities."""

import logging
from typing import Any

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


def log_async_exception(
    exception: Exception,
    scope: dict[str, Any] | None = None,
    severity: str = "error",
    view_name: str = "",
    extra_context: dict | None = None,
) -> None:
    """
    Log an exception raised in an async (Channels/ASGI) context to the database.

    Mirror of ``log_exception`` for ASGI ``scope`` dicts. Used by WebSocket
    consumers and library loggers (daphne / channels) where there is no
    Django ``HttpRequest`` to introspect.

    Args:
        exception: The exception instance.
        scope: The Channels ASGI scope dict (or ``None`` when unavailable, e.g.
               for library logger records).
        severity: Error severity level (debug, info, warning, error, critical).
        view_name: Caller-supplied dotted name (e.g.
                   ``"writeback.consumers.ProposalConsumer.connect"``). Falls
                   back to the empty string if not provided.
        extra_context: Additional context to store under ``request_data["extra"]``.
    """
    # Import here to avoid circular imports on app startup.
    from core.models import ErrorLog

    try:
        stacktrace = get_full_stack()

        error_data: dict[str, Any] = {
            "severity": severity,
            "message": str(exception) or exception.__class__.__name__,
            "exception_type": exception.__class__.__name__,
            "stacktrace": stacktrace,
            "method": "WS",
            "view_name": view_name,
        }

        if scope is not None:
            error_data.update(_scope_request_fields(scope))
            error_data["request_data"] = _scope_request_data(scope, extra_context)
        elif extra_context:
            error_data["request_data"] = {"extra": extra_context}

        ErrorLog.objects.create(**error_data)

    except Exception as e:
        # Last-resort fallback: never let observability code crash the consumer.
        logger.exception("Failed to log async error to database: %s", e)


def _scope_request_fields(scope: dict[str, Any]) -> dict[str, Any]:
    """Map ASGI scope keys onto ErrorLog request-context columns."""
    fields: dict[str, Any] = {}

    raw_path = scope.get("raw_path") or scope.get("path") or ""
    if isinstance(raw_path, bytes):
        raw_path = raw_path.decode("utf-8", errors="replace")
    query_string = scope.get("query_string") or b""
    if isinstance(query_string, bytes):
        query_string = query_string.decode("utf-8", errors="replace")
    scheme = scope.get("scheme", "")
    host = _scope_header(scope, b"host", default="")
    proto = "wss" if scheme == "wss" else ("ws" if scheme == "ws" else scheme or "ws")
    full_url = f"{proto}://{host}{raw_path}"
    if query_string:
        full_url = f"{full_url}?{query_string}"
    fields["url"] = full_url[:500]

    user = scope.get("user")
    if user is not None and getattr(user, "is_authenticated", False):
        fields["user"] = user

    ua = _scope_header(scope, b"user-agent", default="")
    fields["user_agent"] = ua[:500]

    fields["ip_address"] = _scope_client_ip(scope)

    return fields


def _scope_request_data(
    scope: dict[str, Any],
    extra_context: dict | None,
) -> dict[str, Any]:
    """Build the JSON ``request_data`` payload from a scope (no PII)."""
    url_route = scope.get("url_route") or {}
    payload: dict[str, Any] = {
        "scope_type": scope.get("type", ""),
        "scope_path": scope.get("path", ""),
        "url_route_kwargs": _stringify_kwargs(url_route.get("kwargs", {})),
    }
    if extra_context:
        payload["extra"] = extra_context
    return payload


def _scope_header(scope: dict[str, Any], name: bytes, default: str = "") -> str:
    """Pick a single header out of the Channels ``scope["headers"]`` list."""
    for header_name, header_value in scope.get("headers") or ():
        if header_name == name:
            try:
                return header_value.decode("utf-8", errors="replace")
            except AttributeError:
                return str(header_value)
    return default


def _scope_client_ip(scope: dict[str, Any]) -> str | None:
    """Extract the client IP from scope, honouring X-Forwarded-For when present."""
    xff = _scope_header(scope, b"x-forwarded-for", default="")
    if xff:
        return xff.split(",")[0].strip() or None
    client = scope.get("client")
    if isinstance(client, (list, tuple)) and client:
        return str(client[0])
    return None


def _stringify_kwargs(kwargs: dict[str, Any]) -> dict[str, str]:
    """JSON-safe view of the URL route kwargs (UUIDs etc. become strings)."""
    return {k: str(v) for k, v in kwargs.items()}


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
