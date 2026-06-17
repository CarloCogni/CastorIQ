<div align="center">

<img src="src/static/images/castor-logo-nobg.png" alt="CastorIQ" width="120">

# 🦫 CastorIQ

**Talk to your BIM. Edit it back. Track every change.**

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](LICENSE)
[![Status: Public Beta](https://img.shields.io/badge/status-public%20beta-orange)](https://castoriq.io)
[![Python](https://img.shields.io/badge/python-3.12+-blue)](https://www.python.org/)
[![Django](https://img.shields.io/badge/django-5.x-092e20)](https://www.djangoproject.com/)

[**Try the hosted beta**](https://castoriq.io) · [**Watch the demo**](https://www.youtube.com/watch?v=5JB-yFb7k0w) · [**Read the docs**](docs/setup.md)

</div>

---

## Watch the demo — 60 seconds

[![CastorIQ demo](https://i.ytimg.com/vi/5JB-yFb7k0w/maxresdefault.jpg)](https://www.youtube.com/watch?v=5JB-yFb7k0w)

<sub>Click to play on YouTube.</sub>

---

## What is CastorIQ?

In AEC projects, information lives in two disconnected worlds: **BIM models** (IFC files with geometry, properties, spatial hierarchy) and **technical documents** (fire safety reports, thermal specs, structural calculations). When one changes, the other rarely follows — leading to costly errors, rework, and liability.

CastorIQ is a bi-directional LLM assistant that bridges both. Ask cross-cuts the model and the docs and answers with citations. Modify proposes property changes in plain language and routes them through a risk-stratified review pipeline before touching the IFC. Every approved change is a Git commit on a per-project repository.

The hosted beta lives at **[castoriq.io](https://castoriq.io)** — invite-only, manual vetting.

---

## What you can do

**What you do**

- **Ask** — Natural-language queries across the IFC model and any uploaded specs. Answers cite the entity or page they came from.
- **Modify** — Propose property changes in plain English. A three-tier review pipeline keeps low-risk edits fast and high-risk edits reviewable.
- **Explore** — Click through floor plans, points of interest, and uploaded media — no BIM viewer required.

**What backs it**

- **Versioned** — Every accepted modification is a Git commit on a per-project repository. Reverts cost a click.
- **Manage** — Track assets, work orders, permits, and maintenance against the same model your team queries.
- **Yours** — Bring your own Anthropic or Groq key — or run fully on-prem with Ollama. Your IFC stays where you put it.

> Geometry modifications are out of scope. CastorIQ handles properties, materials, classifications, and psets — not the placement of physical elements.

---

## Try the hosted beta

The fastest way to see CastorIQ is the hosted beta at **[castoriq.io](https://castoriq.io)**. It's invite-only — apply through the form on the landing page and a real human reads every application. You'll get a sample IFC project pre-loaded so you can try Ask and Modify in seconds, plus a daily token allowance for cloud LLM use.

---

## Run it yourself

CastorIQ is a Django app with a Postgres + pgvector backend. You need Python 3.12+, Docker, and [`uv`](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/CarloCogni/CastorIQ.git
cd CastorIQ
cp .env.example .env                    # then edit DB / LLM credentials
docker compose up -d                    # Postgres 16 + pgvector
uv sync                                 # install Python deps
uv run src/manage.py migrate
uv run src/manage.py runserver 8001     # http://localhost:8001
```

Long version with prerequisites, Ollama setup, model downloads, and BYOK configuration: **[docs/setup.md](docs/setup.md)**.

---

## Tech stack

| Component | Technology |
|---|---|
| Backend | Django 5.x + Django REST Framework |
| Database | PostgreSQL 16 + pgvector |
| Real-time | Django Channels + Daphne (ASGI) |
| LLM (local) | Ollama (user-selectable) |
| LLM (BYOK) | Anthropic Claude · Groq |
| Embeddings | mxbai-embed-large (1024d) |
| IFC processing | IfcOpenShell |
| Orchestration | LangChain + LangGraph |
| Frontend | Django templates + HTMX + Bootstrap 5 |
| Package manager | UV |
| Containerization | Docker + Docker Compose |

---

## LLM model selection

Each user picks their preferred Ollama model from Settings — the choice persists across sessions and applies to every pipeline (RAG, intent classification, code generation). BYOK users can override per purpose.

| VRAM tier | Example GPUs | Recommended models |
|---|---|---|
| Lite (≤ 6 GB) | GTX 1660, RTX 3060 6GB | `qwen3:0.6b`, `qwen3:1.7b`, `llama3.2:3b` |
| Standard (≤ 8 GB) | RTX 4060, RTX 3060 Ti | `qwen3:4b`, `qwen3:8b`, `llama3.1:8b` |
| Performance (≤ 12 GB) | RTX 4070, RTX 3080 | `qwen3:14b`, `qwen3:30b-a3b` (MoE) |
| High-end (≤ 24 GB) | RTX 4090 | `qwen3:32b`, `qwen3.5:35b` |
| Workstation (48 GB+) | A6000, multi-GPU | `llama3.3:70b`, `qwen3:235b-a22b` (MoE) |

The curated model registry lives in [`src/core/model_registry.py`](src/core/model_registry.py). Models not in the registry still work — they just show as *"Unknown model"* in the UI. The fallback is always `settings.OLLAMA_MODEL` from `.env`.

---

## Documentation

Detailed docs live in [`docs/`](docs/):

- **[Setup guide](docs/setup.md)** — prerequisites, installation, running the project
- **[Architecture](docs/architecture.md)** — system design, data models, data flow
- **[Conventions](docs/conventions.md)** — code style, Django patterns, developer guidelines
- **[UI/UX](docs/ui-ux.md)** — interface design, layout, interaction patterns
- **[Write-back system](docs/writeback/overview.md)** — RSAA framework, tier escalation, agent architecture
- **[RAG pipeline](docs/rag-pipeline.md)** — embedding flow, retrieval, citation rendering
- **[BYOK setup](docs/byok-setup.md)** — bring your own Anthropic or Groq key
- **[Project history](docs/history.md)** — academic origin and design decisions

---

## Contributing

Issues and pull requests welcome. Open an issue first for anything larger than a typo fix — it's a small team and we want to align on scope before code lands. Please follow the [code conventions](docs/conventions.md) and run `uv run ruff check . && uv run ruff format .` before committing.

CastorIQ is AGPL-3.0. Contributions are accepted under the same license.

---

## Team

CastorIQ was built by **Group 4 at [Zigurat Institute of Technology](https://www.e-zigurat.com/)** as part of the MSc in AI for Architecture & Construction (2026):

Pavla Hornická · Carlo Cogni · Islam Mohamed Sabra Ahmed · Maria Makri · Erez Bader

---

## License

CastorIQ is released under the **[GNU Affero General Public License v3.0](LICENSE)**. You can use, modify, and self-host it freely. If you run CastorIQ as a hosted service for others, you must publish your modifications under the same license.

The academic origin is documented in [`docs/history.md`](docs/history.md).
