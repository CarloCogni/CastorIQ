# Testing

This document is the canonical reference for Castor's test strategy. It covers what the test suite protects, how it is structured, how to run it, and the design decisions behind it. The audience is any developer contributing to Castor or onboarding to the codebase. The suite protects the write-back pipeline and filter engine above all else — a silent bug there corrupts IFC files on disk.

---

## Three Categories at a Glance

There are three categories a developer typically thinks about. Here is where each lives in this project and the single command to run it:

| Category | What it covers | Command | Needs |
|---|---|---|---|
| **Python unit & integration** | Service logic, models, views, pipeline | `uv run pytest -m "not slow" -v` | Docker + PostgreSQL |
| **Python E2E (browser)** | Full UI flows, JavaScript execution | `uv run pytest tests/e2e/ -m slow -v` | Docker + PostgreSQL + Chromium |
| **JavaScript** | Inline JS in templates | *(see below)* | — |

**JavaScript has no dedicated test runner.** All JS in Castor is embedded in Django templates (`_ask.html`, `_modify.html`, `chat_message_list.html`). It is exercised indirectly by Playwright E2E tests — a real Chromium browser executes it. There is no Jest/Vitest setup and no separate JS coverage report. See [JavaScript Testing](#javascript-testing) below.

---

## Philosophy

Four principles govern every test in this suite.

**Test the riskiest boundary first.** The `FilterEngine` and the Tier 1 / Tier 2 / Tier 3 pipeline are the highest-stakes code in Castor. A wrong filter targets the wrong entities. A broken tier escalation silently corrupts an IFC file. These services have the deepest test coverage. Model `__str__` methods and view auth checks are tested last.

**Mock at the boundary, not inside.** The LLM (Ollama), the embedding service, Git, and IfcOpenShell are external dependencies — they are always patched at the callsite boundary. Internal service logic (filter resolution, validation, plan structure) is never mocked: we test the real thing. This distinction is load-bearing. If you mock `FilterEngine.resolve()` in a pipeline test, you have tested nothing.

**No `@pytest.mark.django_db` where not needed.** Pure-logic tests (token utils, emitters, message normalizer, registry) carry no DB marker and run in milliseconds without PostgreSQL. DB markers are reserved for tests that genuinely require the ORM. This keeps the fast feedback loop fast.

**Factories over fixtures for model data.** `factory_boy` with `SubFactory` chains builds relational data automatically. A `ModificationProposalFactory` creates a user, a project, an IFC file, and a proposal in a single call — without a fixture file that drifts out of sync with the model.

---

## Test Layers

```
                  ┌─────────────────────────────────────────────────────┐
                  │  Playwright E2E         @pytest.mark.slow            │  ← Browser + live server
                  ├─────────────────────────────────────────────────────┤
                  │  IfcOpenShell integration  @pytest.mark.slow         │  ← Real .ifc file, no browser
                  ├─────────────────────────────────────────────────────┤
                  │  Pipeline integration smoke  @pytest.mark.slow       │  ← CapturingEmitter, DB
                  ├─────────────────────────────────────────────────────┤
                  │  DB-dependent services  @django_db                   │  ← ORM, no file I/O
                  ├─────────────────────────────────────────────────────┤
                  │  LLM-mocked services   (no marker)                   │  ← Patched Ollama
                  ├─────────────────────────────────────────────────────┤
                  │  Pure logic            (no marker)                   │  ← No DB, no I/O
                  └─────────────────────────────────────────────────────┘
```

| Layer | Marker | Needs DB? | Extra prerequisites | Examples |
|---|---|---|---|---|
| Pure logic | _(none)_ | No | — | `test_token_utils`, `test_message_normalizer`, `test_emitters`, `test_llm_model_registry` |
| LLM-mocked | _(none)_ | No | — | `test_triage_classifier`, `test_slot_extractor`, `test_hint_generator`, `test_tier3_executor`, `test_git_service`, `test_token_budget` |
| DB-dependent | `@pytest.mark.django_db` | Yes | Docker + PostgreSQL | `test_filter_engine`, `test_tier1_validator`, `test_models` |
| Pipeline integration smoke | `@pytest.mark.slow` | Yes | Docker + PostgreSQL | `test_modification_pipeline` |
| IfcOpenShell integration | `@pytest.mark.slow` | No | Real `.ifc` fixture (committed) | `test_ifc_writer_integration` |
| Playwright E2E | `@pytest.mark.slow` | Yes | Docker + PostgreSQL + Chromium | `tests/e2e/` |

**Guard:** never use `@pytest.mark.django_db` on a test that does not touch the ORM. Every spurious DB marker slows down the fast suite.

**Never:** mock `FilterEngine`, `Tier1Validator`, or any internal service logic. These are what the tests exist to exercise.

---

## Repository Structure

```
Castor/
  tests/
    conftest.py                 ← Cross-app fixtures: user, project, ifc_file, ifc_entities, mock_llm_response

  src/
    core/tests/
      __init__.py
      factories.py              ← UserFactory, UserLLMConfigFactory
      test_token_utils.py       ← Pure Python — no DB
      test_token_budget.py      ← Patches _fetch_context_window_from_ollama
      test_llm_model_registry.py ← Pure Python — no DB
      test_models.py            ← ErrorLog, UserLLMConfig __str__ and fields

    environments/tests/
      __init__.py
      factories.py              ← ProjectFactory, ProjectMembershipFactory
      test_models.py            ← Project, ProjectMembership __str__, user_has_access()
      test_views.py             ← Auth gates and membership checks

    ifc_processor/tests/
      __init__.py
      factories.py              ← IFCFileFactory (SimpleUploadedFile + file_hash), IFCEntityFactory

    chat/tests/
      __init__.py
      factories.py              ← ChatSessionFactory, MessageFactory
      test_models.py
      test_rag_service.py       ← Patches LLM + EmbeddingService

    writeback/tests/
      __init__.py
      conftest.py               ← Shared writeback fixtures (user, project, ifc_file, wall_entities, door_entity)
      factories.py              ← ModificationProposalFactory, ConflictFactory
      test_message_normalizer.py  ← Pure regex — no DB
      test_emitters.py          ← NullEmitter, CapturingEmitter — no DB
      test_filter_engine.py     ← FilterEngine.resolve() — @django_db
      test_tier1_validator.py   ← Tier1Validator full validation matrix — @django_db
      test_triage_classifier.py ← stage 1 — segment kinds, mock LLM, no DB
      test_slot_extractor.py    ← stage 2 — per-kind slot prompts + guards, mock LLM, no DB
      test_tier_router.py       ← stage 3.5 — deterministic routing, no LLM, no DB
      test_hint_generator.py    ← Tier 0 hints (Templated / Registry / LLM-fallback gating), mock LLM
      test_tier2_planner.py     ← _validate_plan + generate_plan — mock LLM, no DB
      test_tier3_executor.py    ← Code validation only — pure Python, no DB
      test_git_service.py       ← Patches git.Repo — no file I/O
      test_models.py            ← ModificationProposal, GitCommit __str__ and defaults
      test_views.py             ← Approve/reject/apply auth gates
      test_consumers.py         ← Async WebSocket consumer — @pytest.mark.asyncio
      test_modification_pipeline.py  ← Full Tier 1 via CapturingEmitter — @pytest.mark.slow
```

**Root `tests/conftest.py`** provides cross-app fixtures used when the test spans more than one Django app — e.g. a view test that needs a user, project, and IFC file together. **App-level `conftest.py`** (currently only `writeback/tests/conftest.py`) provides specialised fixtures that are only meaningful in that domain, importing factories from sibling apps.

---

## Fixture & Factory Chains

The factory ownership chain mirrors the data model:

```
UserFactory
  └── ProjectFactory(owner=user)
        └── IFCFileFactory(project=project, status="completed")
              └── IFCEntityFactory(ifc_file=ifc_file, ...)
```

`factory_boy` `SubFactory` declarations enforce relational integrity automatically. Creating a `ModificationProposalFactory()` without arguments builds the entire chain — user, project, IFC file, proposal — in one call. Tests that need only a subset pass explicit values:

```python
IFCEntityFactory(ifc_file=ifc_file, ifc_type="IfcWall", name="Wall-001")
```

**`writeback/tests/conftest.py`** builds the standard fixture set shared across all writeback tests:

| Fixture | Provides |
|---|---|
| `user` | A `UserFactory()` instance |
| `project` | `ProjectFactory(owner=user)` |
| `ifc_file` | `IFCFileFactory(project=project, status="completed")` |
| `wall_entities` | 5 `IfcWall` entities with `Pset_WallCommon` properties |
| `door_entity` | 1 `IfcDoor` entity with `Pset_DoorCommon` properties |

**`CapturingEmitter`** (`writeback/services/emitters.py`) is the key tool for pipeline integration tests. It satisfies the `PipelineEmitter` protocol and stores all `emit()` calls in `self.events` — a plain list of dicts. Pipeline smoke tests assert against this list without any WebSocket infrastructure:

```python
emitter = CapturingEmitter()
svc.propose("Set fire rating to EI120 on all walls", user, emitter=emitter)
phases = [e["phase"] for e in emitter.events]
assert "classify" in phases
assert "validate" in phases
```

---

## What NOT to Test

| Component | Reason |
|---|---|
| `Tier3Executor._run_sandboxed()` with real code | Executes arbitrary Python; covered by end-to-end only |
| `GitService.track_ifc()` / `snapshot()` with a real Git repo | Real repo in tests is slow and brittle; gated at unit level by patching `Repo` |
| `ifc_processor.services.parser.*` | Heavy IfcOpenShell parsing; slow integration test only |
| `EmbeddingService.embed()` internals | Wraps Ollama; always mocked at every consumer |
| `ModificationService.apply_proposal()` full execution | Requires IFC file on disk + Git; covered by the smoke test path |
| `GuardianService` standalone | Fully mocked in the pipeline smoke test via `monkeypatch` |
| `ConflictScanService` | Too large a mock surface for meaningful unit tests |
| Django admin | No custom admin logic in this project |

---

## Mocking Reference

All patches use `monkeypatch` or `unittest.mock.patch`. Prefer `monkeypatch` — it is scoped to the test automatically with no decorator stacking.

| Dependency | Patch target |
|---|---|
| LLM | `core.llm.get_llm` |
| Embedding | `embeddings.services.embedding_service.EmbeddingService.embed_query` |
| IfcOpenShell | `ifcopenshell.open` |
| Git | `writeback.services.git_service.Repo` |
| Ollama API (token budget) | `core.token_budget._fetch_context_window_from_ollama` |
| WebSocket send | `patch.object(consumer, 'send_json')` |

**LLM mock pattern** — patch `get_llm` per pipeline-stage module, since each stage instantiates its own LLM lazily on the first call:

```python
@pytest.fixture
def mock_llm():
    mock = MagicMock()
    with (
        patch("writeback.services.triage_classifier.get_llm", return_value=mock),
        patch("writeback.services.slot_extractor.get_llm", return_value=mock),
        patch("writeback.services.entity_resolver.get_llm", return_value=mock),
        patch("writeback.services.tier2_planner.get_llm", return_value=mock),
        patch("writeback.services.tier3_planner.get_llm", return_value=mock),
        patch("writeback.services.tier3_reviewer.get_llm", return_value=mock),
    ):
        yield mock
```

Alternatively, when testing a single stage in isolation, inject a mock via the constructor — the pipeline stages accept an optional `llm=` kwarg specifically for this:

```python
mock_llm = MagicMock()
mock_llm.invoke.return_value.content = json.dumps({"segments": [...]})
classifier = TriageClassifier(llm=mock_llm)
result = classifier.classify("set FireRating to EI120 on all walls")
```

---

## JavaScript Testing

Castor has no separate `.js` files — all JavaScript is inlined inside Django templates using `<script>` blocks. The three templates with significant logic are:

| Template | What the JS does |
|---|---|
| `writeback/tabs/_modify.html` | WebSocket lifecycle, proposal card rendering, progress tracker, HTTP fallback (~600 lines) |
| `environments/tabs/_ask.html` | Optimistic UI, auto-scroll, textarea auto-grow, HTMX lifecycle hooks |
| `environments/components/chat_message_list.html` | Copy-to-clipboard, source card expand/collapse |

### How JS is tested today

Playwright E2E tests launch a real Chromium browser. Any JS bug that breaks a visible interaction will cause an E2E assertion to fail. This is the only JS coverage Castor currently has. There is no Istanbul/V8 coverage report — only Python coverage from `pytest-cov`.

### How to get JS coverage (optional)

Playwright can collect V8 JavaScript coverage from a real browser session. This requires opt-in per test:

```python
# tests/e2e/test_modify_flow.py — example of JS coverage collection
@pytest.mark.slow
def test_websocket_proposal_flow(page: Page, live_server) -> None:
    page.coverage.start_js_coverage()
    # ... test body ...
    coverage = page.coverage.stop_js_coverage()
    # coverage is a list of dicts: {url, text, ranges}
    # Write to disk and process with Istanbul/nyc for an HTML report
```

Because all JS is inline (no source URLs), the coverage output maps to the full rendered HTML page — not individual `.js` files. It is informative but not line-precise the way Python coverage is.

**Practical guidance:** the Playwright E2E tests are the right place to catch JS regressions. Add a test in `tests/e2e/` whenever you add a new interactive behaviour in a template. Dedicated JS unit testing (Jest/Vitest) would require extracting the inline scripts into `src/static/js/*.js` — a worthwhile refactor if the JS grows further.

---

## Running Tests

All commands run from the **project root** (`Castor/`), not `src/`.

```bash
# Prerequisite: PostgreSQL must be running for @django_db tests
docker compose -f docker/docker-compose.yml up -d

# Full suite (no slow tests) with HTML coverage report
uv run pytest --cov=src --cov-report=term-missing --cov-report=html -m "not slow"

# Skip slow integration tests only (DB tests still run)
uv run pytest -m "not slow"

# Fast suite only — skip DB and slow integration tests
uv run pytest -m "not slow" --ignore=src/writeback/tests/test_filter_engine.py \
    --ignore=src/writeback/tests/test_tier1_validator.py

# Pure-logic tests only (no DB, no Docker required)
uv run pytest src/core/tests/ \
    src/writeback/tests/test_message_normalizer.py \
    src/writeback/tests/test_emitters.py \
    src/writeback/tests/test_tier3_executor.py \
    src/writeback/tests/test_git_service.py

# Single test file
uv run pytest src/writeback/tests/test_filter_engine.py -v

# Single test by name
uv run pytest src/writeback/tests/test_filter_engine.py -k "test_empty_filter" -v

# App-specific coverage with missing-line report
uv run pytest src/writeback/tests/ --cov=src/writeback --cov-report=term-missing -v

# Open HTML coverage report (Windows)
start htmlcov/index.html
```

### IfcOpenShell integration tests

These tests call IfcOpenShell with no mocks against a real `.ifc` fixture committed to the repo. They do not require PostgreSQL or a browser — just the Python environment.

```bash
# All IfcOpenShell integration tests
uv run pytest src/ifc_processor/tests/test_ifc_writer_integration.py -v

# Via marker (also picks up any future slow tests in ifc_processor/)
uv run pytest src/ifc_processor/tests/ -m slow -v
```

The fixture file lives at `src/ifc_processor/tests/fixtures/simple_wall.ifc` — two `IfcWall` entities with `Pset_WallCommon`. Each test copies it to a `tmp_path` so the committed file is never modified.

### Playwright E2E tests

These tests launch a real Chromium browser against a live Django server. They exercise the full stack — routing, templates, JavaScript, and WebSocket fallback — with LLM endpoints intercepted via `page.route()`.

**One-time setup:**
```bash
uv run playwright install chromium
```

**Running:**
```bash
# All E2E tests (headless)
uv run pytest tests/e2e/ -m slow -v

# Watch tests execute in a visible browser window
uv run pytest tests/e2e/ -m slow -v --headed

# Single E2E file
uv run pytest tests/e2e/test_modify_flow.py -m slow -v

# All slow tests at once (integration + E2E)
uv run pytest -m slow -v
```

**Prerequisites for E2E:** Docker + PostgreSQL running, Django's `live_server` fixture handles the dev server automatically — no need to start it manually.

---

## Reading Coverage

The HTML coverage report (`htmlcov/index.html`) opens in a browser. Click any file to see line-by-line coverage.

**What to look for:**

- **Red lines in `writeback/services/`** — highest risk. Uncovered branches in `filter_engine.py`, `tier1_validator.py`, or `modification_service.py` mean potential silent IFC corruption.
- **Red lines in `core/token_budget.py`** — context overflow guard. Uncovered fallback branches risk LLM context silently truncating.
- **Red lines in views** — usually missing auth-gate tests. Lower risk but important for security.

**Acceptable targets per layer:**

| Layer | Target coverage |
|---|---|
| Pure logic (token utils, normalizer) | 100% |
| LLM-mocked services (classifier, tier3, git) | ≥ 85% |
| DB-dependent services (filter engine, validator) | ≥ 80% |
| Views | ≥ 70% (auth gates covered) |
| Integration smoke | Not counted in coverage — presence is the signal |

Coverage targets are guidance, not gates. A 100%-covered bad test is worse than a 60%-covered honest test.

---

## Design Decisions

1. **pytest over unittest.** Fixture composability via `conftest.py` and `pytest.fixture` eliminates class inheritance ceremony. Fixtures can be composed, parameterised, and scoped without subclassing `TestCase`.

2. **factory_boy over manual `Model.objects.create()`**. `SubFactory` chains enforce relational integrity automatically. When a model gains a required field, updating the factory propagates to all tests. Manually constructed objects drift.

3. **`CapturingEmitter` for pipeline integration.** The `PipelineEmitter` protocol decouples the pipeline from its transport. Smoke tests use `CapturingEmitter` to assert the phase sequence without WebSocket infrastructure, ASGI channels, or any async machinery. This is both faster and more reliable than a full channel layer test for the pipeline logic.

4. **`monkeypatch` preferred over `@patch` decorator.** `monkeypatch` is scoped to the test function automatically — no `patcher.start()` / `patcher.stop()` boilerplate, no decorator stacking, no fixture ordering surprises. `@patch` is used only when `monkeypatch` cannot reach the target (e.g., `patch.object` on an already-constructed instance).

5. **Root `tests/conftest.py` for cross-app fixtures; app-level `conftest.py` for specialised fixtures.** The root conftest provides the standard shared objects (`user`, `project`, `ifc_file`, `ifc_entities`) used across multiple apps. The writeback-level conftest builds writeback-specific fixtures (`wall_entities`, `door_entity`) that import from sibling app factories. This prevents circular imports and keeps the root conftest minimal.

---

See also: `.claude/skills/testing-skill.md` — code-generation patterns for this test suite.
