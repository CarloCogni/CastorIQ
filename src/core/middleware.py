"""Custom middleware for error logging"""

from django.utils.deprecation import MiddlewareMixin

from .exceptions import log_exception


class ErrorLoggingMiddleware(MiddlewareMixin):
    """Middleware to log all unhandled exceptions to database"""

    def process_exception(self, request, exception):
        """
        Called when a view raises an exception.

        This catches ALL exceptions in views and logs them to the database.
        Returns None to let Django's default error handling continue.
        """
        # Determine severity based on exception type
        if isinstance(exception, (KeyError, ValueError, AttributeError)):
            severity = "error"
        elif isinstance(exception, PermissionError):
            severity = "warning"
        else:
            severity = "critical"

        # Log to database - get_full_stack() will capture everything
        log_exception(exception, request=request, severity=severity)

        # Return None to let Django handle the exception normally
        return None
