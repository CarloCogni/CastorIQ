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



# =============================================================================
# Worktree DB auto-detection (MUST be last — overrides DATABASES when active)
# When running inside a Claude Code worktree with an isolated DB,
# this overrides DATABASES to point at the worktree's container.
# In normal operation (no .env.worktree file), this does nothing.
# =============================================================================
def _detect_worktree_db():
    from pathlib import Path

    current = Path(__file__).resolve().parent
    for directory in [current, current.parent, current.parent.parent, current.parent.parent.parent]:
        env_file = directory / ".env.worktree"
        if env_file.exists():
            config = {}
            for line in env_file.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    key, _, value = line.partition("=")
                    config[key.strip()] = value.strip()
            return config
    return None


_wt = _detect_worktree_db()
if _wt:
    print(f"[worktree] Isolated Castor DB on port {_wt.get('WORKTREE_DB_PORT')}")
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": _wt.get("WORKTREE_DB_NAME", "castor"),
            "USER": _wt.get("WORKTREE_DB_USER", "castor"),
            "PASSWORD": _wt.get("WORKTREE_DB_PASSWORD", "castor"),
            "HOST": _wt.get("WORKTREE_DB_HOST", "localhost"),
            "PORT": _wt.get("WORKTREE_DB_PORT", "5432"),
        }
    }
