"""Core views."""


from django.http import JsonResponse
from django.shortcuts import redirect
from django.contrib.auth.decorators import login_required

def health_check(request):
    """Health check endpoint."""
    return JsonResponse({"status": "healthy", "service": "castor"})


@login_required
def home_view(request):
    """Home page - redirect to projects."""
    return redirect("projects:list")