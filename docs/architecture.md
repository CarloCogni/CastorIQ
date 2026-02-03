# Castor Architecture

## Overview

Castor is a bi-directional LLM assistant that bridges IFC models and technical documentation.

## Components

### 1. Environments
User workspaces containing IFC files and related documents.

### 2. IFC Processor
Parses IFC files using IfcOpenShell, extracts entities and properties.

### 3. Documents
Processes PDF and DOCX files, chunks text for embedding.

### 4. Embeddings
RAG pipeline using pgvector for semantic search.

### 5. Chat
Conversational interface for queries and commands.

### 6. Writeback
Proposes and applies IFC modifications with human approval.

## Data Flow

```
User Query
    │
    ▼
Intent Classification
    │
    ├── Query → RAG Pipeline → Response
    │
    └── Command → Entity Resolution → Modification Proposal
                                            │
                                            ▼
                                    Human Approval
                                            │
                                            ▼
                                    Apply via IfcOpenShell
                                            │
                                            ▼
                                    Git Commit
```
