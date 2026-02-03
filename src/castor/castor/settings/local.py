"""Local development settings."""

from .base import *  # noqa: F401, F403

DEBUG = True

# Database - Local PostgreSQL via Docker
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": "castor",
        "USER": "castor",
        "PASSWORD": "castor",
        "HOST": "localhost",
        "PORT": "5432",
    }
}

# Allow all hosts in development
ALLOWED_HOSTS = ["*"]

# Django Debug Toolbar (optional)
# INSTALLED_APPS += ["debug_toolbar"]
# MIDDLEWARE.insert(0, "debug_toolbar.middleware.DebugToolbarMiddleware")
# INTERNAL_IPS = ["127.0.0.1"]
