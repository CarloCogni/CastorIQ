# documents/routing.py
"""WebSocket URL routing for the documents app (OCR streaming)."""

from django.urls import re_path

from documents import consumers

websocket_urlpatterns = [
    re_path(
        r"ws/projects/(?P<project_id>[0-9a-f-]+)/documents/(?P<document_id>[0-9a-f-]+)/ocr/$",
        consumers.OCRConsumer.as_asgi(),
    ),
]
