"""ASGI config for Castor project."""

import os

from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import AllowedHostsOriginValidator
from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")

# Must call get_asgi_application() before importing anything that touches Django ORM
django_asgi_app = get_asgi_application()

from documents.routing import websocket_urlpatterns as documents_ws  # noqa: E402
from writeback.routing import websocket_urlpatterns as writeback_ws  # noqa: E402

application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": AllowedHostsOriginValidator(
            AuthMiddlewareStack(URLRouter(writeback_ws + documents_ws))
        ),
    }
)
