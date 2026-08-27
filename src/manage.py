#!/usr/bin/env python
"""Django command-line utility for administrative tasks."""

import asyncio
import os
import sys

# Daphne/Twisted deadlocks on Windows with the default ProactorEventLoop
# (Python 3.12+). Force SelectorEventLoop so sync_to_async works correctly.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def main():
    """Run administrative tasks."""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError("Couldn't import Django. Are you sure it's installed?") from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
