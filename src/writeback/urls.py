"""Project URL configuration."""

from django.urls import path

from . import views

app_name = "writeback"

urlpatterns = [

    # path('projects/<uuid:project_id>/proposals/<uuid:proposal_id>/verify/', views.VerifyProposalView.as_view(), name='verify_proposal'),
]