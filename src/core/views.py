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


def test_error(request):
    """Test view to trigger an error - REMOVE IN PRODUCTION"""
    raise ValueError("This is a test error to verify error logging works! \n #### Hasta la vista Baby!!!! ####")


