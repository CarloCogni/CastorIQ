---
name: testing
description: >
  Write and maintain pytest tests for the Castor Django project. Trigger this skill whenever
  the user asks to: write tests, add test coverage, create a test file, test a service/model/view/consumer,
  generate fixtures, or when modifying any module and tests should be updated to match. Also trigger when
  the user says "test", "coverage", "pytest", "conftest", "factory", "mock", or asks Claude Code to
  verify changes don't break anything. Use this skill even if the user just pastes a class and says
  "test this". Always read this skill before writing any test code.
---

# Testing Skill — Castor Project

## Stack

- **pytest** + **pytest-django** (no `unittest.TestCase` subclassing)
- **factory_boy** for model fixtures
- **unittest.mock.patch** for external dependencies (LLM, Ollama, Git, IFC parsing, embeddings)
- **pytest-asyncio** for async consumers and services

## Project Layout

```
src/
  <app>/
    tests/
      __init__.py
      conftest.py          ← app-level fixtures & factories
      test_models.py
      test_services.py     ← one file per service if multiple
      test_views.py
      test_consumers.py    ← async WebSocket tests (writeback only)
      test_admin.py        ← only if custom admin logic exists
      factories.py         ← factory_boy model factories
```

If the `tests/` directory doesn't exist for an app yet, create it with `__init__.py`.

A root-level `conftest.py` at `src/conftest.py` should hold cross-app fixtures (e.g. authenticated user, project with IFC file).

## Test File Conventions

### Naming

```python
def test_<method_or_behavior>_<scenario>_<expected_result>():
```

Examples:
```python
def test_classify_single_set_property_returns_tier1_intent()
def test_classify_invalid_json_response_raises_intent_parse_error()
def test_build_entity_context_empty_entities_returns_no_entities()
def test_proposal_approve_already_applied_raises_validation_error()
```

### Structure (Arrange-Act-Assert)

Every test follows AAA with a one-line docstring:

```python
def test_classify_rename_request_uses_set_attribute(mock_llm):
    """SET_ATTRIBUTE is chosen when user says 'rename', not SET_PROPERTY."""
    # Arrange
    classifier = IntentClassifier(user=None)
    mock_llm.return_value = json.dumps({
        "tier": 1, "operation": "SET_ATTRIBUTE",
        "filter": {"ifc_type": "IfcWall", "name_pattern": "W-01"},
        "attribute": "Name", "new_value": "W-01-Updated",
        "confidence": 0.95, "explanation": "Rename wall"
    })

    # Act
    result = classifier.classify("Rename wall W-01 to W-01-Updated", "IfcWall (1): W-01")

    # Assert
    assert result["operation"] == "SET_ATTRIBUTE"
    assert "pset" not in result
```

### Markers

```python
@pytest.mark.django_db          # Only when hitting the real DB
@pytest.mark.asyncio             # Async consumer / service tests
@pytest.mark.slow                # Integration tests > 2s (LLM calls, IFC parsing)
@pytest.mark.parametrize(...)    # Use for operation types, tier variants, severity levels
```

Register custom markers in `pyproject.toml`:
```toml
[tool.pytest.ini_options]
markers = [
    "slow: marks tests as slow (deselect with '-m not slow')",
]
```

## What to Mock — ALWAYS

These external boundaries must NEVER be called in unit tests:

| Dependency | Mock target |
|---|---|
| LLM (Ollama via LangChain) | `unittest.mock.patch.object(classifier, 'llm')` or `patch('core.llm.get_llm')` |
| Embedding generation | `patch('embeddings.services.embedding_service.EmbeddingService.embed')` |
| IFC file I/O (IfcOpenShell) | `patch('ifc_processor.services.parser.ifcopenshell.open')` |
| Git operations | `patch('writeback.services.git_service.Repo')` |
| WebSocket send | `patch.object(consumer, 'send_json')` or mock the emitter |
| File system (IFC files on disk) | `patch('builtins.open')` or use `tmp_path` fixture |
| Django Channels layer | `patch('channels.layers.get_channel_layer')` |

## Factory Boy Patterns

Factories go in `<app>/tests/factories.py`:

```python
import factory
from django.contrib.auth import get_user_model

User = get_user_model()


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = User
    username = factory.Sequence(lambda n: f"user_{n}")
    email = factory.LazyAttribute(lambda o: f"{o.username}@test.com")


class ProjectFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "environments.Project"
    name = factory.Sequence(lambda n: f"Project {n}")
    created_by = factory.SubFactory(UserFactory)


class IFCFileFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "ifc_processor.IFCFile"
    project = factory.SubFactory(ProjectFactory)
    uploaded_by = factory.LazyAttribute(lambda o: o.project.created_by)
    original_filename = "test_model.ifc"


class IFCEntityFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "ifc_processor.IFCEntity"
    ifc_file = factory.SubFactory(IFCFileFactory)
    ifc_type = "IfcWall"
    name = factory.Sequence(lambda n: f"Wall-{n:03d}")
    global_id = factory.Faker("uuid4")
    properties = factory.LazyFunction(lambda: {
        "Pset_WallCommon.IsExternal": True,
        "Pset_WallCommon.FireRating": "EI60",
    })


class ModificationProposalFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "writeback.ModificationProposal"
    ifc_file = factory.SubFactory(IFCFileFactory)
    created_by = factory.LazyAttribute(lambda o: o.ifc_file.uploaded_by)
    request_text = "Set fire rating to EI120"
    explanation = "Change FireRating on matched walls"
    changes = factory.LazyFunction(list)
    diff_preview = "FireRating: EI60 → EI120"
    status = "pending"
    tier = 1
    operation = "SET_PROPERTY"
```

Adapt and extend these as needed. Always check actual model fields before generating a factory — don't guess nullable/required fields.

## Test Categories — What to Cover

### 1. Service Classes (highest priority)

For every public method, test:

- **Happy path**: valid input → correct output
- **Edge cases**: empty input, None, boundary values, empty QuerySets
- **Failure modes**: dependency raises exception, LLM returns garbage, IFC file missing
- **Input validation**: wrong types, missing required args

For LLM-dependent services (IntentClassifier, GuardianService, Tier2Planner, Tier3Executor, ConflictScanService, RAGService):
- Mock the LLM response, test parsing logic and downstream behavior
- Test malformed LLM output (invalid JSON, missing fields, unexpected tier)
- Test confidence normalization (0.0-1.0 → 0-100 conversion)

### 2. Models

- Field constraints: required fields, max_length, choices validation
- `__str__` output
- Custom methods and properties
- Status transitions (e.g., Proposal: pending → approved → applied)
- Meta: ordering, indexes, unique constraints
- Relationships: cascading deletes, SET_NULL behavior

### 3. Views / API Endpoints

- Authentication required (redirect or 403)
- Permission checks (project membership)
- GET returns correct context / queryset
- POST with valid data → expected side effect
- POST with invalid data → form errors, no side effect
- Use `client` fixture from pytest-django

### 4. WebSocket Consumers (writeback app only)

- Test with `channels.testing.WebsocketCommunicator`
- Verify connection accepted/rejected based on auth
- Verify correct messages are sent for each pipeline phase
- Test error handling (service throws → error message sent, connection not dropped)

## Running Tests

After writing or modifying tests, always run them:

```bash
cd src && python -m pytest <app>/tests/ -v --tb=short
```

For a single file:
```bash
cd src && python -m pytest <app>/tests/test_services.py -v --tb=short
```

After modifying ANY service or model, run its corresponding tests:
```bash
cd src && python -m pytest <app>/tests/ -v --tb=short -x
```

The `-x` flag stops on first failure — fix it before moving on.

## Anti-Patterns — DO NOT

1. **Don't test private methods directly** — test through public interface
2. **Don't write tests that pass by coincidence** — assert specific values, not just "no exception"
3. **Don't use `assertTrue(result)`** — assert the actual expected shape/value
4. **Don't create tests that depend on execution order** — each test is independent
5. **Don't mock too deep** — mock at the boundary (LLM, filesystem, network), not internal helpers
6. **Don't write a test without a docstring** — if you can't explain WHY, the test probably doesn't matter
7. **Don't use `@pytest.mark.django_db` when no DB is needed** — pure logic tests should run without DB
8. **Don't test Django/DRF framework behavior** — test YOUR code, not that `models.CharField` works

## Checklist Before Committing Tests

- [ ] All tests pass: `python -m pytest <app>/tests/ -v`
- [ ] No DB marker on pure logic tests
- [ ] All LLM / external calls are mocked
- [ ] Each test has a docstring explaining WHAT and WHY
- [ ] Factories match current model fields (check after model changes)
- [ ] No hardcoded absolute paths or machine-specific values