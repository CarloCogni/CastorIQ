"""Core views."""

from django.http import JsonResponse


def health_check(request):
    """Health check endpoint."""
    return JsonResponse({"status": "healthy", "service": "castor"})
