# core/logging_handlers.py
"""Logging handlers that route library log records into ``ErrorLog``.

The HTTP path is already covered by ``ErrorLoggingMiddleware``, and the
WebSocket consumer entry points by ``capture_consumer_errors``. Neither
sees library-level events emitted by ``daphne``, ``channels``,
``channels.server`` etc. — protocol errors, "Application instance was
lost" warnings, JSON encode failures inside Channels' own machinery.

This handler bridges that gap: ERROR/CRITICAL records on the configured
library loggers land as ``ErrorLog`` rows so the operator has a single
"what's broken" surface for both HTTP and async paths.

Defensive contract:
    * Never raises. A logging-handler failure must not crash the
      consumer or HTTP request that emitted the record.
    * Skips records emitted before ``apps.ready`` — the model layer
      isn't usable yet during the import phase.
    * Truncates fields to model-column widths.

Known limitation:
    When ``logger.error(...)`` fires from inside an event loop (e.g. a
    Daphne / Channels library log emitted during async traffic), the
    synchronous ORM call inside :meth:`emit` will trip Django's
    async-safety check. The handler swallows that error via
    ``handleError`` and the row is dropped — the parallel console
    handler still captures the line, so the operator can find it in
    container stdout. Most library WARNING/ERROR records originate
    from sync threads (Daphne worker startup, configuration errors)
    and reach ErrorLog fine; this gap only affects records emitted
    from inside the running ASGI event loop. Revisit if loss is
    observed in practice.
"""

from __future__ import annotations

import logging
import traceback


class ErrorLogDBHandler(logging.Handler):
    """Persist log records (default: WARNING+) into ``core.models.ErrorLog``.

    Wired in ``settings.LOGGING.handlers`` and attached to library
    loggers that Castor cannot otherwise observe. The default level
    on the handler itself is WARNING; per-logger ``level`` settings
    in the LOGGING dict still gate which records reach the handler.
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            from django.apps import apps

            if not apps.ready:
                # Django isn't fully booted (early import-time logging).
                # Writing to the ORM here would raise; drop the record.
                return

            from core.models import ErrorLog

            stacktrace, exception_type, message = self._extract_payload(record)

            ErrorLog.objects.create(
                severity=self._severity_for(record.levelno),
                message=(message or "(empty log message)")[:5000],
                exception_type=exception_type[:255],
                stacktrace=stacktrace,
                method="",
                view_name=record.name[:255],
                request_data={
                    "logger": record.name,
                    "level": record.levelname,
                    "module": getattr(record, "module", ""),
                    "func": getattr(record, "funcName", ""),
                    "pathname": getattr(record, "pathname", ""),
                    "lineno": getattr(record, "lineno", None),
                },
            )
        except Exception:
            # ``Handler.handleError`` honours ``logging.raiseExceptions``
            # and never raises in production — exactly what we need.
            self.handleError(record)

    @staticmethod
    def _extract_payload(record: logging.LogRecord) -> tuple[str, str, str]:
        """Pull (stacktrace, exception_type, message) out of a log record."""
        exc_info = record.exc_info if isinstance(record.exc_info, tuple) else None
        if exc_info and exc_info[1] is not None:
            stacktrace = "".join(traceback.format_exception(*exc_info))
            exception_type = exc_info[1].__class__.__name__
            message = str(exc_info[1]) or record.getMessage()
            return stacktrace, exception_type, message

        # No exception attached — the record is a plain logger.error/warning
        # call. Synthesise a stacktrace from the call site so the operator
        # can still see where it came from.
        stack = "".join(traceback.format_stack(limit=12))
        return stack, record.levelname, record.getMessage()

    @staticmethod
    def _severity_for(levelno: int) -> str:
        """Map ``logging`` levels onto ``ErrorLog.Severity`` choices."""
        if levelno >= logging.CRITICAL:
            return "critical"
        if levelno >= logging.ERROR:
            return "error"
        if levelno >= logging.WARNING:
            return "warning"
        if levelno >= logging.INFO:
            return "info"
        return "debug"
