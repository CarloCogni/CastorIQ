# chat/routing.py
"""WebSocket URL routing for the Ask (RAG) tab."""

from django.urls import re_path

from chat import consumers

websocket_urlpatterns = [
    re_path(
        r"ws/projects/(?P<project_id>[0-9a-f-]+)/ask/$",
        consumers.AskConsumer.as_asgi(),
    ),
]
