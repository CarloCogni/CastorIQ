# beta/urls.py
"""Public + admin-adjacent URLs for the beta funnel."""

from django.urls import path

from . import views

app_name = "beta"

urlpatterns = [
    # Application form POST target. The landing page itself lives at "/" and
    # is rendered by core.views.home_view; this is the receiving endpoint.
    path("apply/", views.apply_view, name="apply"),
]
