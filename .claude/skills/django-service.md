.claude/skills/django-service.md
# Skill: Django Service Layer

Read before creating or modifying any service class.

## Pattern

```python
# app/services/name.py
import logging
logger = logging.getLogger(__name__)

class SomeService:
    def __init__(self, project, user):
        self.project = project
        self.user = user

    def do_thing(self, param: str) -> dict:
        if not param:
            return {"result": None, "error": "param required"}
        try:
            result = self._step(param)
            logger.info("done: %s", param)
            return {"result": result, "error": None}
        except ValueError as e:
            logger.error("failed: %s", e)
            return {"result": None, "error": str(e)}
```

Rules: per-request instantiation, constructor takes project+user, return dicts,
guard clauses first, try/except around external calls, type hints on public methods.

---