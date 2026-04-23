# facilities/routing.py
"""WebSocket URL routing for the Facilities app."""

from django.urls import re_path

from facilities import consumers

websocket_urlpatterns = [
    re_path(
        r"ws/projects/(?P<project_id>[0-9a-f-]+)/fm/export/$",
        consumers.ExportConsumer.as_asgi(),
    ),
]
