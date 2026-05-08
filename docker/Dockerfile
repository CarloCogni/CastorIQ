# docker/Dockerfile — Castor production image (Daphne + ASGI)
#
# Builds a single-process Daphne container that nginx fronts via the prod
# compose stack. WSGI is intentionally not used: the writeback Modify pipeline
# streams over WebSocket, and gunicorn would silently drop those upgrades.

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DJANGO_SETTINGS_MODULE=config.settings.production \
    PATH="/root/.local/bin:${PATH}"

WORKDIR /app

# Build-time + runtime system deps.
#   libpq-dev / gcc: psycopg & ifcopenshell wheel fallbacks
#   libgl1 / libglib2.0-0: PyMuPDF and OpenCV runtime
#   curl: certbot/healthcheck convenience inside the container
#   git: GitPython (writeback/services/git_service.py) probes for the git
#        binary at import time — without it, every Django command crashes
#        because writeback/views.py is imported during URL resolution.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq-dev \
        gcc \
        libgl1 \
        libglib2.0-0 \
        curl \
        git \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# uv handles the lockfile-driven install. We never install the project itself
# (--no-install-project): Castor is a Django app, not a distributed package.
RUN pip install --no-cache-dir uv

# Copy lockfile + manifest first so dependency layer caches independently
# of source edits.
COPY pyproject.toml uv.lock ./

RUN uv sync --frozen --no-install-project --no-dev

# Bring uv's venv onto PATH for the rest of the build + runtime.
ENV PATH="/app/.venv/bin:${PATH}"

# Application source. Static assets live under src/static/ and per-app
# */static/ dirs — collectstatic below pulls from STATICFILES_DIRS into
# STATIC_ROOT (set to /app/staticfiles in production.py to line up with the
# named volume mounted by docker-compose.prod.yml).
COPY src/ /app/src/
COPY fixtures/ /app/fixtures/

# Build-time-only env so collectstatic + system checks don't fail on
# missing prod secrets. Real runtime values come from docker-compose env_file.
RUN DJANGO_SECRET_KEY=build-only-not-used-at-runtime \
    POSTGRES_PASSWORD=build-only \
    DJANGO_ALLOWED_HOSTS=localhost \
    python src/manage.py collectstatic --noinput --clear

EXPOSE 8000

# Daphne serves both HTTP and WebSocket through the ASGI application.
# Working directory is /app/src so `config.asgi:application` resolves.
WORKDIR /app/src
CMD ["daphne", "-b", "0.0.0.0", "-p", "8000", "config.asgi:application"]
