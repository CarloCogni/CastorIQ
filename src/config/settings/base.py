"""Base Django settings for Castor project."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Build paths inside the project like this: BASE_DIR / "subdir".
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "django-insecure-change-me-in-production")

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = os.getenv("DJANGO_DEBUG", "True").lower() == "true"

ALLOWED_HOSTS = os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")

# Application definition
INSTALLED_APPS = [
    "daphne",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third party
    "rest_framework",
    "crispy_forms",
    "crispy_bootstrap5",
    "channels",
    # Local apps
    "core",
    "environments",
    "chat",
    "ifc_processor",
    "documents",
    "embeddings",
    "writeback",
    "metacastor",
    "eastereggs",
    "facilities",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "core.middleware.ErrorLoggingMiddleware",  # ADD THIS LINE
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.llm_context",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Internationalization
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# Authentication
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "projects:list"
LOGOUT_REDIRECT_URL = "login"

# Static files (CSS, JavaScript, Images)
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [
    BASE_DIR / "static",
]

# Media files
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

# File upload settings
FILE_UPLOAD_MAX_MEMORY_SIZE = 50 * 1024 * 1024  # 50 MB
DATA_UPLOAD_MAX_MEMORY_SIZE = 50 * 1024 * 1024  # 50 MB

# Default primary key field type
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# REST Framework
REST_FRAMEWORK = {
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
}

# Ollama Configuration
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b")
OLLAMA_EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "mxbai-embed-large")

# RAG Token Budget
RAG_RESPONSE_RESERVE = int(os.getenv("RAG_RESPONSE_RESERVE", "1500"))
RAG_SAFETY_RATIO = float(os.getenv("RAG_SAFETY_RATIO", "0.90"))

# Vector Configuration
PGVECTOR_DIMENSIONS = int(os.getenv("PGVECTOR_DIMENSIONS", "1024"))

# GLM-OCR Configuration
# GLM_OCR_ENABLED=False makes the entire subsystem a no-op with zero pipeline side effects.
GLM_OCR_ENABLED = os.getenv("GLM_OCR_ENABLED", "False").lower() == "true"
GLM_OCR_MODEL = os.getenv("GLM_OCR_MODEL", "glm-ocr:latest")
GLM_OCR_OLLAMA_URL = os.getenv("GLM_OCR_OLLAMA_URL", OLLAMA_HOST)
GLM_OCR_TEXT_DENSITY_THRESHOLD = int(os.getenv("GLM_OCR_TEXT_DENSITY_THRESHOLD", "50"))
GLM_OCR_AUTO_TRIGGER = os.getenv("GLM_OCR_AUTO_TRIGGER", "True").lower() == "true"
GLM_OCR_MAX_PAGES_AUTO = int(os.getenv("GLM_OCR_MAX_PAGES_AUTO", "100"))
GLM_OCR_PAGE_DPI = int(os.getenv("GLM_OCR_PAGE_DPI", "150"))

# File Upload Configuration
IFC_UPLOAD_DIR = os.getenv("IFC_UPLOAD_DIR", "uploads/ifc")
DOCUMENT_UPLOAD_DIR = os.getenv("DOCUMENT_UPLOAD_DIR", "uploads/documents")

# Crispy Forms
CRISPY_ALLOWED_TEMPLATE_PACKS = "bootstrap5"
CRISPY_TEMPLATE_PACK = "bootstrap5"

# External Services
# Supabase
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_PUBLISHABLE_KEY = os.getenv("SUPABASE_PUBLISHABLE_KEY", "")
