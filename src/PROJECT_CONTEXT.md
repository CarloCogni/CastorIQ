# Castor - Project Context for LLM Assistance

> **Purpose:** This document provides full context for continuing development with an LLM assistant.
> **Last Updated:** 6 February 2026
> **Developer:** Carlo Cogni (Lead Developer, Group 4)
> **Project:** Final Master's Project - MSc in AI for Architecture & Construction, Zigurat Institute of Technology

---

## 1. Project Overview

### What is Castor?
Castor is a **bi-directional LLM assistant** that bridges IFC (Industry Foundation Classes) models and technical documentation in the AEC (Architecture, Engineering, Construction) industry.

### The Problem ("Split Reality")
In AEC projects, information exists in two separate worlds:
- **BIM Models (IFC files):** 3D building data with properties, spatial hierarchy, elements
- **Technical Documents (PDF/DOCX):** Specifications, contracts, reports, standards

These often become **out of sync**, leading to costly errors, conflicts, and rework.

### The Solution
Castor provides:
1. **Ask Mode:** Query both IFC models AND documents using natural language (RAG)
2. **Modify Mode:** Propose changes to IFC files via natural language → approval flow → Git commit
3. **Conflict Detection:** Automatically detect inconsistencies between IFC and documents
4. **Version Control:** Every approved modification creates a Git commit with full traceability

### Key Innovation
**Bi-directional sync:** Not just reading from IFC, but **writing back** to IFC files with human approval and version control.

---

## 2. Technical Stack

### Backend
| Component | Technology                                   | Why                                          |
|-----------|----------------------------------------------|----------------------------------------------|
| Framework | Django 5.x                                   | Rapid development, ORM, admin, battle-tested |
| Database | PostgreSQL 16 + pgvector                     | Vector similarity search for RAG             |
| LLM | Ollama (local) with llama3.1:8b              | Privacy, no API costs, runs on most PCs      |
| Embeddings | mxbai-embed-large (1024 dimensional vectors) | Good quality, runs locally                   |
| IFC Processing | IfcOpenShell                                 | Industry standard, read AND write IFC        |
| PDF Processing | PyMuPDF (planned)                            | Fast, reliable text extraction               |
| Task Queue | None yet (synchronous)                       | Could add Celery later for async             |

### Frontend
| Component | Technology | Why |
|-----------|------------|-----|
| CSS Framework | Bootstrap 5 (dark theme) | Known by team, rapid development |
| Icons | Bootstrap Icons | Consistent with Bootstrap, 2000+ icons |
| Forms | django-crispy-forms + crispy-bootstrap5 | Clean form templates |
| Interactivity | HTMX | Django-friendly, minimal JavaScript |
| Templates | Django templates (app-based structure) | Simple, portable apps |

### Infrastructure
| Component | Technology |
|-----------|------------|
| Containerization | Docker + Docker Compose |
| Version Control | Git (for code AND for IFC file versions) |
| IDE | PyCharm Community |
| Package Manager | uv (fast Python package manager) |

---

## 3. Architecture

### Project Structure
```
Castor/
├── src/                          # Django project root
│   ├── config/                   # Project configuration
│   │   ├── settings/
│   │   │   ├── base.py          # Shared settings
│   │   │   ├── local.py         # Development settings
│   │   │   └── production.py    # Production settings
│   │   ├── urls.py
│   │   └── wsgi.py
│   ├── core/                     # Base models, utilities
│   │   ├── models.py            # UUIDModel, TimestampedModel
│   │   ├── admin.py
│   │   └── templates/core/
│   │       ├── base.html        # Base template with Bootstrap 5
│   │       └── registration/login.html
│   ├── environments/             # Projects/workspaces
│   │   ├── models.py            # Project, ProjectMembership
│   │   ├── views.py             # List, Detail, Tabs, Upload
│   │   ├── urls.py
│   │   └── templates/environments/
│   │       ├── project_list.html
│   │       ├── project_detail.html
│   │       └── tabs/_ask.html, _modify.html, etc.
│   ├── ifc_processor/            # IFC parsing and entities
│   │   ├── models.py            # IFCFile, IFCEntity
│   │   ├── services/
│   │   │   ├── parser.py        # IfcOpenShell parsing
│   │   │   └── validators.py    # IFC file validation
│   │   └── management/commands/parse_ifc.py
│   ├── documents/                # PDF/DOCX processing
│   │   ├── models.py            # Document, DocumentChunk
│   │   └── services/            # (to be built)
│   ├── chat/                     # Conversations
│   │   └── models.py            # ChatSession, Message, MessageFeedback
│   ├── embeddings/               # Vector embeddings (to be built)
│   └── writeback/                # IFC modifications
│       └── models.py            # ModificationProposal, GitCommit, Conflict
├── docker-compose.yml            # PostgreSQL + pgvector
├── pyproject.toml
└── .env                          # Environment variables
```

### Database Models

#### Core
- `UUIDModel`: Abstract base with UUID primary key + timestamps
- `TimestampedModel`: Abstract base with created_at/updated_at

#### Environments
- `Project`: Workspace with owner, collaborators, git_repo_path
- `ProjectMembership`: User roles (viewer/editor/admin)

#### IFC Processor
- `IFCFile`: Uploaded IFC with status, hash, entity_count
- `IFCEntity`: Extracted entity with global_id, ifc_type, properties (JSON), embedding (vector 768D)

#### Documents
- `Document`: Uploaded PDF/DOCX with status, page_count
- `DocumentChunk`: Text chunk with content, page_number, embedding (vector 768D)

#### Chat
- `ChatSession`: Conversation with mode (ask/modify), project, user
- `Message`: Individual message with role (user/assistant/system), retrieved_context (JSON)
- `MessageFeedback`: Thumbs up/down on responses

#### Writeback
- `ModificationProposal`: Proposed IFC change with status (pending/approved/rejected/applied), diff_preview
- `GitCommit`: Record of applied change with commit_hash, entities modified/added/removed
- `Conflict`: Detected inconsistency between IFC and documents with severity, resolution status

### Key Design Decisions

1. **UUID Primary Keys:** All models use UUIDs for security and distributed-friendly IDs
2. **Proper Indexing:** Database indexes on frequently queried fields (status, project, created_at)
3. **select_related/prefetch_related:** Used in views to avoid N+1 queries
4. **App-based Templates:** Each app has its own templates folder (`app/templates/app/`)
5. **Service Layer:** Business logic in `services/` modules, not in views
6. **Validation:** IFC files validated for extension AND content (STEP header check)

---

## 4. Current Progress

### ✅ Completed
- [x] Development environment (Docker, PostgreSQL, pgvector, Ollama)
- [x] Django project structure with proper settings split
- [x] All database models with verbose_name, help_text, indexes
- [x] Admin interfaces for all models with fieldsets, actions, filters
- [x] URL routing for projects and tabs
- [x] Views with proper query optimization
- [x] Templates with Bootstrap 5 dark theme
- [x] Authentication (login/logout)
- [x] Mode-based UI (Ask, Modify, Conflicts, History tabs)
- [x] File upload with validation (extension + content check + duplicate detection)
- [x] IFC parsing service (IfcOpenShell) with entity extraction
- [x] Semantic description generation for RAG
- [x] Team setup guide document

### 🔄 In Progress
- [ ] Test IFC parsing with real IFC files

### ❌ Not Started
- [ ] Document processing (PDF text extraction)
- [ ] Text chunking strategy
- [ ] Embeddings service (Ollama mxbai-embed-large)
- [ ] Vector storage and similarity search
- [ ] RAG pipeline (retrieval + generation)
- [ ] Ask tab functionality (end-to-end)
- [ ] Modify tab functionality (proposal generation)
- [ ] IFC write-back
- [ ] Git integration for IFC versioning
- [ ] Conflict detection logic
- [ ] HTMX real-time updates

---

## 5. UI/UX Design

### Mode-Based Tabs
```
┌────────────────────────────────────────────────────┐
│  [💬 Ask]  [✏️ Modify]  [⚠️ Conflicts]  [📜 History]  │
└────────────────────────────────────────────────────┘
```

- **Ask:** Read-only queries about IFC/documents (chat interface)
- **Modify:** Propose changes → approval flow → Git commit
- **Conflicts:** Dashboard of detected inconsistencies with severity badges
- **History:** Git commit log with diff viewing

### Layout
- Left sidebar: IFC files and documents list with upload buttons
- Main area: Tab content (chat or dashboard)
- Dark theme with Castor blue (#3b82f6) as primary color

### Design Principles
- Minimalist, professional appearance
- No 3D viewer (complex IFC models better viewed in Blender+Bonsai)
- Clean code (Uncle Bob principles)
- Efficient database queries

---

## 6. RAG Architecture (Planned)

### Indexing Pipeline
```
IFC File → Parse → Extract Entities → Generate Description → Embed → Store in pgvector
Document → Extract Text → Chunk → Embed → Store in pgvector
```

### Query Pipeline
```
User Question → Embed → Similarity Search (IFC entities + Document chunks)
                     → Build Context → LLM Generate Response
                     → Return with Source Citations
```

### Embedding Model
- **Model:** mxbai-embed-large via Ollama
- **Dimensions:** 768
- **Storage:** pgvector extension in PostgreSQL

### LLM
- **Model:** llama3.1:8b via Ollama
- **Context:** 8K tokens
- **Temperature:** Low for factual queries, moderate for explanations

---

## 7. Modification Flow (Planned)
```
User: "Change all doors on Level 1 to fire-rated"
           │
           ▼
   ┌───────────────┐
   │ Parse Request │
   └───────┬───────┘
           │
           ▼
   ┌───────────────┐
   │ Find Entities │ ← RAG search for doors on Level 1
   └───────┬───────┘
           │
           ▼
   ┌───────────────┐
   │ Generate Diff │ ← Show what will change
   └───────┬───────┘
           │
           ▼
   ┌───────────────┐
   │ User Approval │ ← Approve / Reject / Edit
   └───────┬───────┘
           │
           ▼
   ┌───────────────┐
   │ Apply Changes │ ← IfcOpenShell write
   └───────┬───────┘
           │
           ▼
   ┌───────────────┐
   │  Git Commit   │ ← Version control
   └───────────────┘
```

---

## 8. Environment Setup

### Docker Services
```yaml
services:
  db:
    image: pgvector/pgvector:pg16
    ports: 5432:5432
    environment:
      POSTGRES_DB: castor
      POSTGRES_USER: castor
      POSTGRES_PASSWORD: castor_dev_password
```

### Ollama Models
```bash
ollama pull llama3.1:8b        # Main LLM (4.9 GB)
ollama pull mxbai-embed-large   # Embeddings (274 MB)
```

### Key Environment Variables (.env)
```
DEBUG=True
SECRET_KEY=your-secret-key
DATABASE_URL=postgres://castor:castor_dev_password@localhost:5432/castor
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=llama3.1:8b
OLLAMA_EMBED_MODEL=mxbai-embed-large
```

### Run Commands
```bash
# Start database
docker compose -f docker/docker-compose.yml up -d

# Run Django
cd src
uv run manage.py runserver 8001

# Parse IFC files
uv run manage.py parse_ifc --all-pending
```

---

## 9. Future Work / Nice-to-Have

### Short Term (For FMP Demo - May 3rd)
- [ ] Complete RAG pipeline
- [ ] Basic Ask functionality working
- [ ] Basic Modify functionality with one example
- [ ] Show conflict detection concept

### Medium Term
- [ ] Async processing with Celery
- [ ] HTMX for real-time chat updates
- [ ] Better diff visualization
- [ ] Rollback functionality

### Long Term / Research
- [ ] **Fine-tuning** LLM on AEC terminology and IFC schema
- [ ] Multi-model support (Claude API as option)
- [ ] IFC schema validation for modifications
- [ ] Integration with common BIM platforms
- [ ] Collaborative editing (multiple users)

---

## 10. Known Limitations

1. **Synchronous Processing:** IFC parsing blocks the request (could add Celery)
2. **No 3D Visualization:** Users must use external viewers for complex models
3. **Local LLM Only:** Currently no cloud API option (privacy-first design)
4. **Single File Git:** Git tracks individual IFC files, not model relationships
5. **English Only:** UI and LLM prompts are English-only

---

## 11. Code Conventions

### Python
- Type hints where practical
- Docstrings for services and complex functions
- f-strings for formatting
- Pathlib for file paths

### Django
- Class-based views with mixins
- Service layer for business logic
- select_related/prefetch_related for queries
- Proper Meta classes with verbose_name, indexes

### Templates
- Bootstrap 5 utility classes
- Minimal custom CSS (use CSS variables)
- HTMX for interactivity

---

## 12. Useful Commands
```bash
# Django
uv run manage.py makemigrations
uv run manage.py migrate
uv run manage.py createsuperuser
uv run manage.py runserver 8001
uv run manage.py parse_ifc --all-pending
uv run manage.py dump_context  # Generate this context file

# Docker      
      # Start PostgreSQL
docker compose -f docker/docker-compose.yml up -d # Start PostgreSQL
docker-compose down       # Stop
docker-compose logs db    # View logs

# Ollama
ollama list               # Show installed models
ollama run llama3.1:8b    # Test LLM
```

---

## 13. How to Continue Development

When starting a new chat session, provide:

1. **This document** (PROJECT_CONTEXT.md)
2. **The code dump** (project_context.txt from dump_context command)
3. **Specific task** you want to accomplish

Example prompt:
```
I'm continuing development on Castor, a bi-directional LLM assistant for IFC models.

[Paste PROJECT_CONTEXT.md content]

Current code is attached/below.

Next task: [Describe what you want to build]

---------------------------
IMPORTANT DEVELOPER's NOTES: I want the code clean ( like Uncle Bob wants ) and efficient, be DRY ( Do not reapeat yourself).
Leave usefull comments and logging message all in english. AT the beginning of each file always put the name of same 
file in commments like # environments/view.py, or <!--chat_message_list.html-->.
Use Negative Space Programming as much as possible with teqniques such as: Disegning by Omission, Clarity thourgh "White Space",
Minimalist API Desgin, Handling the "Edge" and Removing "Boilerplate" Noise ( For Django would be Context Managers).
Views and Forms should be "dumb", the business logic should be outside ( Processor class, or processing layer ).
acted as a super software developer, keeping in mind the Zen of Python.
As a general rule i'd prefere you pasting here the code in chunk rather than giving me the files directly. I'll make the 
patch manually in my IDE so that i retain full controll and understanding of what i'm doing.
Also when it comes to modifying code, be super precise in where and how to modify it ( especially with html) rather then 
just saying somethign like: "add this, or change that"
Before starting giving code, explain to me the problem and solution to make sure you understand what I am asking.
----------------------------
```

---

## 14. Contact & Repository

- **Developer:** Carlo Cogni
- **Institution:** Zigurat Institute of Technology
- **Program:** MSc in AI for Architecture & Construction
- **GitHub:** https://github.com/CarloCogni/castor (if public)

---

*This document should be updated as the project evolves.*