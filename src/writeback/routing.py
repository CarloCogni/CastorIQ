# writeback/routing.py
"""WebSocket URL routing for the writeback app."""

from django.urls import re_path

from writeback import consumers

websocket_urlpatterns = [
    re_path(
        r"ws/projects/(?P<project_id>[0-9a-f-]+)/modify/$",
        consumers.ProposalConsumer.as_asgi(),
    ),
    re_path(
        r"ws/projects/(?P<project_id>[0-9a-f-]+)/conflicts/scan/$",
        consumers.ScanConsumer.as_asgi(),
    ),
]
