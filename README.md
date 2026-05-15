c# 🦫 Castor

**Bi-Directional LLM Assistant: Bridging the Gap Between IFC Models and Technical Reporting**

Castor is an intelligent assistant that synchronizes Building Information Models (IFC) with technical documentation, enabling natural language queries and AI-proposed model modifications with full version control.

---

## The Problem

In AEC projects, information lives in two disconnected worlds: **BIM models** (IFC files with geometry, properties, spatial hierarchy) and **technical documents** (fire safety reports, thermal specs, structural calculations). When one changes, the other doesn't follow — leading to costly errors, rework, and liability.

## The Solution

Castor bridges both worlds through:

- **Ask Mode** — Query IFC models and documents using natural language (RAG)
- **Modify Mode** — Propose IFC changes via natural language → human approval → Git commit
- **Conflict Detection** — Flag inconsistencies between model data and document requirements
- **Version Control** — Every approved modification creates a Git commit with full traceability

## Tech Stack

| Component | Technology |
|---|---|
| Backend | Django 5.x + Django REST Framework |
| Database | PostgreSQL 16 + pgvector |
| LLM | Ollama (user-selectable, local) |
| Embeddings | mxbai-embed-large (1024d) |
| IFC Processing | IfcOpenShell |
| Orchestration | LangChain + LangGraph |
| Frontend | Django Templates + HTMX + Bootstrap 5 |
| Package Manager | UV |
| Containerization | Docker + Docker Compose |

## LLM Model Selection

Castor supports per-user LLM model selection. Each user can choose their preferred Ollama model from the Settings page — the choice persists across sessions and applies to all pipelines (RAG, intent classification, code generation).

| VRAM Tier | Example GPUs | Recommended Models |
|---|---|---|
| Lite (≤ 6 GB) | GTX 1660, RTX 3060 6GB | `qwen3:0.6b`, `qwen3:1.7b`, `llama3.2:3b` |
| Standard (≤ 8 GB) | RTX 4060, RTX 3060 Ti | `qwen3:4b`, `qwen3:8b`, `llama3.1:8b` |
| Performance (≤ 12 GB) | RTX 4070, RTX 3080 | `qwen3:14b`, `qwen3:30b-a3b` (MoE) |
| High-End (≤ 24 GB) | RTX 4090 | `qwen3:32b`, `qwen3.5:35b` |
| Workstation (48 GB+) | A6000, multi-GPU | `llama3.3:70b`, `qwen3:235b-a22b` (MoE) |

The curated model registry lives in `core/model_registry.py`. Models not in the registry still work — they simply show 
as "Unknown model" in the UI. The fallback model is always `settings.OLLAMA_MODEL` from `.env`.

## Documentation

All detailed documentation lives in [`docs/`](docs/):

- **[Setup Guide](docs/setup.md)** — Prerequisites, installation, running the project
- **[Architecture](docs/old/architecture.md)** — System design, data models, data flow
- **[Conventions](docs/conventions.md)** — Code style, Django patterns, developer guidelines
- **[UI/UX](docs/ui-ux.md)** — Interface design, layout, interaction patterns
- **[Write-Back System](docs/writeback/overview.md)** — RSAA framework, tier escalation, agent architecture

## Team

**Group 4 — Zigurat Institute of Technology**

Pavla Hornická · Carlo Cogni · Islam Mohamed Sabra Ahmed · Maria Makri · Erez Bader

## License

Final Master's Project — MSc in AI for Architecture & Construction, Zigurat Institute of Technology. All rights reserved.