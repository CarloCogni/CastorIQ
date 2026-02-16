# core/views.py
"""Core views."""
from django.http import JsonResponse
from django.shortcuts import redirect
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.contrib.auth.decorators import user_passes_test

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

@user_passes_test(lambda u: u.is_staff)
def loader_gallery(request):
    """
    A gallery to preview all Castor Loader variants.
    Only accessible by Staff.
    """
    return render(request, 'loaders/loader_gallery.html')

def test_landing_page(request):
    """Test view to trigger an error - REMOVE IN PRODUCTION"""
    return render(request, 'registration/login-matrix.html')
