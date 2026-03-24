# Castor — Daily Use Guide

This guide covers everything you need to do to start using Castor each day, apply updates from a teammate, and shut down cleanly.

---

## Before You Begin (One-Time Setup)

Make sure you have already completed the full installation in `docs/setup.md`. You need:

- **Docker Desktop** installed and running
- **Ollama** installed
- **Git** installed
- The project cloned somewhere on your machine (e.g. ~/Projects/Castor/ )
- A `.env` file in the project root (copy from `.env.example` if missing)

---

## Every Day: Starting Castor

Open a terminal (PowerShell or Command Prompt on Windows, Terminal on Mac) in the project folder. 
Then run the following steps **in order**.

### Step 1 — Start Docker Desktop

Open Docker Desktop from your Start Menu / Applications and wait until it shows **"Docker Desktop is running"** in the 
taskbar. This takes about 30 seconds.

> If Docker Desktop is already open and shows a green icon, skip this step.

---

### Step 2 — Start the Database

In your terminal, from the **project root** (the folder that contains `docker/`):

```bash
docker compose -f docker/docker-compose.yml up -d
```

You should see output ending with something like `Started`. The database is now running in the background.

**NB:** this command is needed only the first time to setup the DB. Afterwards you can launch the DB directly from Docker APP

---

### Step 3 — Start Ollama

Open a **new terminal window** (you can leave the previous one open) and run:

```bash
ollama serve
```

Leave this terminal open. You should see a message like `Listening on 127.0.0.1:11434` — that means Ollama is ready.

---

### Step 4 — Start the Castor Web Server

Open a **new terminal window** in the project root and run:

```bash
uv run src/manage.py runserver 8001
```

Leave this terminal open. You will see log messages appear here as you use the app.

---

### Step 5 — Open Castor in Your Browser

Go to:

```
http://localhost:8001
```

Log in with your username and password. If you don't have an account yet, ask the person who set up the project to create one for you.

---

## Getting Updates from a Teammate

When a teammate has pushed new code, do the following before starting the server.

### Step 1 — Pull the latest code

From the **project root**:

```bash
git pull
```

### Step 2 — Apply database changes (if any)

```bash
cd src
uv run manage.py migrate
```

It's safe to run this even if there are no new changes — it will simply say "No migrations to apply."

### Step 3 — Start normally

Follow the "Every Day: Starting Castor" steps above.

---

## Troubleshooting

### "Unable to connect" when opening localhost:8001
The web server is not running. Go to Step 4 in the startup sequence.

### "connection refused" or database errors in the terminal
The database container is not running. Go to Step 2 in the startup sequence. Also make sure Docker Desktop is open.

### LLM responses are empty or very slow
Ollama is not running. Go to Step 3 in the startup sequence and open the Ollama app.

### "No module named X" or import errors
Your virtual environment may not be active or packages are missing. From the project root run:
```bash
uv sync
```
Then try starting the server again.

---

## Quick Reference

| What | Command | Where to run |
|---|---|---|
| Start database | `docker compose -f docker/docker-compose.yml up -d` | Project root |
| Stop database | `docker compose -f docker/docker-compose.yml down` | Project root |
| Start server | `uv run manage.py runserver 8001` | `src/` folder |
| Pull updates | `git pull` | Project root |
| Apply DB changes | `uv run manage.py migrate` | `src/` folder |
| Castor in browser | `http://localhost:8001` | — |
| Check Ollama | `http://localhost:11434` | — |
