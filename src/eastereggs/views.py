# eastereggs/views.py
"""Views for the eastereggs gallery and individual games.

The gallery (``GalleryView``) renders every Game entry from ``registry.py`` as
a card. Individual games are served by dedicated views so each game can do its
own setup (project auth checks, context injection) without a generic dispatcher.
"""

import logging

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views import View

from environments.models import Project

from .registry import GAMES, get_game

logger = logging.getLogger(__name__)


class GalleryView(LoginRequiredMixin, View):
    """Gallery of all easter-egg games — a hidden page linkable from anywhere."""

    def get(self, request):
        game_cards = [
            {
                "slug": g.slug,
                "title": g.title,
                "subtitle": g.subtitle,
                "accent_color": g.accent_color,
                "play_url": reverse(f"eastereggs:{g.url_name}_standalone"),
            }
            for g in GAMES
        ]
        return render(
            request,
            "eastereggs/gallery.html",
            {
                "games": game_cards,
                "page_title": "Eastereggs",
            },
        )


class CastorSlugView(LoginRequiredMixin, View):
    """Castor Slug shooter — either standalone (no project) or project-linked.

    When ``project_id`` is provided, the game listens for scan-phase events
    posted via ``window.postMessage`` from the opener tab.
    """

    def get(self, request, project_id=None):
        game = get_game("castor-slug")

        context: dict = {
            "game": game,
            "project": None,
            "has_scan_link": False,
        }

        if project_id is not None:
            from environments.services import ProjectAccessService

            project = get_object_or_404(Project.objects.select_related("owner"), pk=project_id)
            if not ProjectAccessService.can_access(request.user, project):
                return HttpResponseForbidden("Access denied.")
            context["project"] = project
            context["has_scan_link"] = True

        return render(request, "eastereggs/games/castor_slug.html", context)
