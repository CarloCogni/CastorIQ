# Setup Guide

## Prerequisites

| Tool | Purpose | Install |
|---|---|---|
| Python 3.11+ | Runtime | [python.org](https://python.org) |
| UV | Package manager | `pip install uv` |
| Docker Desktop | PostgreSQL container | [docker.com](https://www.docker.com/products/docker-desktop) |
| Ollama | Local LLM inference | [ollama.ai](https://ollama.ai) |
| Git | Version control | [git-scm.com](https://git-scm.com) |

## Step-by-Step Installation

### 1. Clone the Repository
```bash
git clone https://github.com/CarloCogni/castor.git
cd castor
```

### 2. Start PostgreSQL + pgvector
```bash
docker compose -f docker/docker-compose.yml up -d
```

This starts PostgreSQL 16 with the pgvector extension on port 5432.

### 3. Pull Ollama Models
```bash
ollama pull llama3.1:8b          # Main LLM (~4.9 GB)
ollama pull mxbai-embed-large    # Embedding model (~274 MB)
```

Ollama runs natively (not in Docker) for GPU acceleration.

### 4. Create Virtual Environment
```bash
uv venv

# Windows:
.venv\Scripts\activate
# Linux/Mac:
source .venv/bin/activate

uv pip install -e ".[dev]"
```

### 5. Configure Environment
```bash
cp .env.example .env
```

Default `.env` values for local development:
```
DEBUG=True
SECRET_KEY=your-secret-key
DATABASE_URL=postgres://castor:castor_dev_password@localhost:5432/castor
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=llama3.1:8b
OLLAMA_EMBED_MODEL=mxbai-embed-large
```

### 6. Initialize Database
```bash
cd src
uv run manage.py migrate
uv run manage.py createsuperuser
```

### 7. Run Development Server
```bash
uv run manage.py runserver 8001
```

Visit http://localhost:8001

> **ASGI / WebSocket:** Daphne is listed first in `INSTALLED_APPS`, so Django automatically uses it as the ASGI server. WebSocket endpoints (`/ws/...`) for the Modify pipeline and Conflict Scan will be active with the standard `runserver` command — no separate Daphne process required in development.

---

## Useful Commands

### Django
```bash
uv run manage.py makemigrations
uv run manage.py migrate
uv run manage.py createsuperuser
uv run manage.py runserver 8001
uv run manage.py parse_ifc --all-pending
```

### Docker
```bash
docker compose -f docker/docker-compose.yml up -d    # Start
docker compose -f docker/docker-compose.yml down      # Stop
docker compose -f docker/docker-compose.yml down -v   # Stop + wipe data !!!!!!!!!!!!!
docker compose -f docker/docker-compose.yml logs db   # View logs
```

### Ollama
```bash
ollama list                # Show installed models
ollama run llama3.1:8b     # Interactive test
```

### Code Quality
```bash
pytest                     # Run tests
ruff check .               # Lint
ruff format .              # Format
```

---

## Database Reset
```bash
docker compose -f docker/docker-compose.yml down -v #  -v = wiping all data, careful !!!!
docker compose -f docker/docker-compose.yml up -d
cd src
uv run manage.py migrate
uv run manage.py createsuperuser
```