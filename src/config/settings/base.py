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
    "solo",
    # Local apps — users must come first so AUTH_USER_MODEL resolves before
    # any other migration with a swappable_dependency on it.
    "users",
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
    "beta",
    # 4D Insights module
    "islam",
    "islam.ifc_insights",
    "islam.scheduling",
    "islam.ifc_viewer",
    # Login lockout for /admin/ and /accounts/login. Must come after
    # django.contrib.auth so its signals are loaded first.
    "axes",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "core.middleware.ErrorLoggingMiddleware",  # ADD THIS LINE
    # AxesMiddleware MUST be the very last entry — it inspects the response
    # to detect login failures and lock out abusive IPs.
    "axes.middleware.AxesMiddleware",
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
                "core.context_processors.maintenance_banner",
                "core.context_processors.token_budget",
                "core.context_processors.storage_quota",
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
AUTH_USER_MODEL = "users.User"

# Custom backend lets users sign in with EITHER username or email. With the
# unique constraint on User.email this never collides; on miss the backend
# runs a dummy ``set_password`` so wrong-email and wrong-password share wall
# clock cost (anti-enumeration).
AUTHENTICATION_BACKENDS = [
    # AxesStandaloneBackend MUST be first — it short-circuits authentication
    # for locked-out IPs before the real backend is consulted, so wrong-
    # password attempts past the failure limit don't leak timing info.
    "axes.backends.AxesStandaloneBackend",
    "core.auth_backends.EmailOrUsernameModelBackend",
]

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "projects:list"
LOGOUT_REDIRECT_URL = "login"

# Password reset — beta uses Django's built-in PasswordResetConfirmView as the
# one-time set-password link in welcome emails (M3.6). Default is 7 days so a
# reviewer can sit on an approval over the weekend without invalidating the
# applicant's link.
PASSWORD_RESET_TIMEOUT = int(os.getenv("PASSWORD_RESET_TIMEOUT", str(7 * 24 * 3600)))

# Email — env-driven so dev can stay on console output and prod can swap to
# SMTP without a code change. EMAIL_HOST being unset is the signal that no
# real backend is configured; prod overrides EMAIL_BACKEND to smtp explicitly
# in production.py.
EMAIL_BACKEND = os.getenv("EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
EMAIL_HOST = os.getenv("EMAIL_HOST", "")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = os.getenv("EMAIL_USE_TLS", "True").lower() == "true"
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "Castor <noreply@castoriq.io>")
SERVER_EMAIL = os.getenv("SERVER_EMAIL", DEFAULT_FROM_EMAIL)

# Operator inbox that receives a one-line ping every time someone submits the
# beta application form. Empty = notifications disabled (dev default). The
# applicant always gets their confirmation regardless.
OPERATOR_NOTIFICATION_EMAIL = os.getenv("OPERATOR_NOTIFICATION_EMAIL", "")

# Public-facing site URL — used to build absolute links in welcome emails.
SITE_URL = os.getenv("SITE_URL", "http://localhost:8001")

# Caches — ``default`` is per-process LocMemCache (template fragments, ad-hoc
# memoisation). ``throttle`` is Postgres-backed via DatabaseCache so per-IP
# rate limits and the daily send-count circuit breaker share state across
# every Daphne worker. Run ``manage.py createcachetable`` once to provision.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "castor-default",
    },
    "throttle": {
        "BACKEND": "django.core.cache.backends.db.DatabaseCache",
        "LOCATION": "castor_throttle_cache",
    },
}

# django-ratelimit reads its counters from this cache alias.
RATELIMIT_USE_CACHE = "throttle"

# django-axes — login lockout for /admin/ and any login view. After
# AXES_FAILURE_LIMIT failed attempts from one IP, that IP is locked for
# AXES_COOLOFF_TIME hours. Successful logins reset the counter.
AXES_FAILURE_LIMIT = int(os.getenv("AXES_FAILURE_LIMIT", "5"))
AXES_COOLOFF_TIME = int(os.getenv("AXES_COOLOFF_TIME", "1"))
AXES_LOCKOUT_PARAMETERS = ["ip_address"]
AXES_RESET_ON_SUCCESS = True
AXES_VERBOSE = False
# Tells axes to read the client IP from HTTP_X_FORWARDED_FOR rather than
# REMOTE_ADDR. Set to 1 in production where nginx is the only ingress; leave
# at 0 in dev so unit tests can't spoof IPs via the header.
AXES_IPWARE_PROXY_COUNT = int(os.getenv("AXES_IPWARE_PROXY_COUNT", "0"))

# Beta funnel anti-abuse — public form rate limit and Brevo daily-budget
# circuit breaker. Counters live in the ``throttle`` cache; both caps are
# total daily Brevo sends (confirmation + operator pings combined).
BETA_RATE_LIMIT = os.getenv("BETA_RATE_LIMIT", "5/h")
BETA_DAILY_TOTAL_CAP = int(os.getenv("BETA_DAILY_TOTAL_CAP", "290"))
BETA_DAILY_OPERATOR_CAP = int(os.getenv("BETA_DAILY_OPERATOR_CAP", "250"))

# Static files (CSS, JavaScript, Images)
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [
    BASE_DIR / "static",
    BASE_DIR / "islam" / "frontend",
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
# Batch size for Ollama embedding requests. Sending hundreds of chunks in a
# single HTTP call overflows Ollama's request limits and returns 400.
OLLAMA_EMBED_BATCH_SIZE = int(os.getenv("OLLAMA_EMBED_BATCH_SIZE", "16"))
# Per-request timeout (seconds) applied to every ChatOllama call. A hung Ollama
# request otherwise wedges the ASGI thread pool and blocks cancellation.
OLLAMA_REQUEST_TIMEOUT = float(os.getenv("OLLAMA_REQUEST_TIMEOUT", "120"))

# Cloud LLM Providers (beta launch)
# Provider selection per call site (Ask vs Modify) is the SiteLLMConfig singleton's
# job at runtime; these env vars are the boot defaults the singleton seeds itself with
# the first time it's read. Local Ollama remains a first-class option at any moment.
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
ASK_PROVIDER = os.getenv("ASK_PROVIDER", "ollama")  # ollama | anthropic | groq
ASK_MODEL = os.getenv("ASK_MODEL", "claude-sonnet-4-6")
MODIFY_PROVIDER = os.getenv("MODIFY_PROVIDER", "ollama")  # ollama | anthropic | groq
MODIFY_MODEL = os.getenv("MODIFY_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")
# Last-resort circuit-breaker. When set, the dispatcher refuses every cloud call and
# the site renders a "paused for maintenance" banner. Local Ollama still works.
LLM_MASTER_KILL = os.getenv("LLM_MASTER_KILL", "0") == "1"

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

# Logging
# Project loggers go to console at INFO so per-entity narratives (scan loop,
# RAG pipeline, modification tiers) are visible during development. Library
# loggers (httpx, ollama, langchain) stay at WARNING so they don't drown app
# signal — without this, every Ollama request emits multiple DEBUG lines and
# the scan trace is impossible to read.
#
# Daphne / Channels stay on the console handler only. ``ErrorLogDBHandler``
# does a synchronous ORM write, which trips ``SynchronousOnlyOperation`` when
# the record is emitted from inside the running ASGI event loop — i.e. exactly
# when a Daphne or Channels WARNING fires during a WebSocket handshake. The
# handler's own try/except swallows the failure, but every dropped record
# still pays an exception round-trip, and the surface area for taking down a
# mid-handshake worker is not worth the (currently zero) operator value of
# DB-side library logs. Revisit once the handler is async-safe.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "concise": {
            "format": "[{levelname}] {name}: {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "concise",
        },
    },
    "loggers": {
        "writeback": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "chat": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "core": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "documents": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "ifc_processor": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "facilities": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "environments": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "metacastor": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "embeddings": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "httpx": {"level": "WARNING"},
        "httpcore": {"level": "WARNING"},
        "ollama": {"level": "WARNING"},
        "langchain_ollama": {"level": "WARNING"},
        "asyncio": {"level": "WARNING"},
        "daphne": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "daphne.server": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "channels": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "channels.server": {"handlers": ["console"], "level": "WARNING", "propagate": False},
    },
}

# ── Writeback rejection-hint generator ─────────────────────────────
# Strategy 3 (LLM-fallback) is wired but gated behind a category whitelist
# that starts empty. Strategies 1 (Templated) and 2 (Registry-grounded) are
# always on and add no LLM cost. Add categories to ``WRITEBACK_HINT_LLM_CATEGORIES``
# only after observing real rejections that 1+2 cannot address — see
# ``writeback/services/hint_generator.py`` for the strategy contracts.
WRITEBACK_HINT_LLM_FALLBACK = True
WRITEBACK_HINT_LLM_CATEGORIES: tuple[str, ...] = ()
