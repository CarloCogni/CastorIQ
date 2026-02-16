# writeback/views.py
"""Writeback views — handled by environments.views.ModifyView."""

from django.views import View
from django.shortcuts import get_object_or_404
from writeback.services.guardian_service import GuardianService
from .models import ModificationProposal
from django.http import JsonResponse

