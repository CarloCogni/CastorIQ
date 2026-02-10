# 🦫 Castor

**Bi-Directional LLM Assistant: Bridging the Gap Between IFC Models and Technical Reporting**

Castor is an intelligent assistant that synchronizes Building Information Models (IFC) with technical documentation, enabling natural language queries and AI-proposed model modifications.

---

## Features

- **Model-to-Text (Forward Flow)**: Query IFC models using natural language
- **Text-to-Model (Reverse Flow)**: Propose IFC modifications from requirements
- **Conflict Detection**: Validate consistency between documents and models
- **Version Control**: Git-based IFC change tracking with full audit trail

---

## Tech Stack

| Component | Technology |
|-----------|------------|
| Backend | Django 5.x + Django REST Framework |
| Database | PostgreSQL 16 + pgvector |
| LLM | Ollama (Llama 3 8B) |
| Embeddings | mxbai-embed-large |
| IFC Processing | IfcOpenShell |
| Orchestration | LangChain + LangGraph |
| Package Manager | UV |
| Containerization | Docker + Docker Compose |

---

## Quick Start

### Prerequisites

- Python 3.11+
- Docker Desktop
- Ollama (native installation for GPU support)
- Git
- UV (`pip install uv`)

### 1. Clone the Repository

```bash
git clone https://github.com/YOUR_USERNAME/castor.git
cd castor
```

### 2. Start Services (PostgreSQL)

```bash
docker compose -f docker/docker-compose.yml up -d
```

### 3. Set Up Ollama (Native - for GPU acceleration)

```bash
# Install Ollama from https://ollama.ai
# Then pull required models:
ollama pull llama3.1:8b
ollama pull mxbai-embed-large
```

### 4. Create Virtual Environment and Install Dependencies

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
# Edit .env with your settings (defaults work for local dev)
```

### 6. Initialize Database

```bash
cd src/castor
python manage.py migrate
python manage.py createsuperuser
```

### 7. Run Development Server

```bash
python manage.py runserver
```

Visit http://localhost:8000

---

## Project Structure

```
castor/
├── docker/                    # Docker configurations
│   ├── docker-compose.yml     # Local dev services
│   └── docker-compose.prod.yml
├── src/castor/               # Django project
│   ├── castor/               # Project settings
│   ├── core/                 # Shared utilities
│   ├── environments/         # Workspace management
│   ├── chat/                 # Chat sessions
│   ├── ifc_processor/        # IFC parsing
│   ├── documents/            # Document processing
│   ├── embeddings/           # RAG pipeline
│   └── writeback/            # IFC modifications
├── tests/                    # Test suite
├── docs/                     # Documentation
└── pyproject.toml           # Dependencies
```

---

## Development Commands

### Running Tests

```bash
pytest
```

### Code Formatting

```bash
ruff check .
ruff format .
```

### Database Reset

```bash
docker compose -f docker/docker-compose.yml down -v
docker compose -f docker/docker-compose.yml up -d
cd src/castor
python manage.py migrate
```

---

## Team

**Group 4 - Zigurat Institute of Technology**

- Pavla Hornicka 
- Carlo Cogni 
- Islam Mohamed Sabra Ahmed 
- Maria Makri 
- Erez Bader 

---

## License

This project is part of the Final Master's Project for the MSc in AI for Architecture & Construction by Zigurat Institute of Technology.
All rights reserved