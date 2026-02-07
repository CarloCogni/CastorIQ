# Castor - Command Reference

Quick reference for common commands. Run these from PyCharm's Terminal.

---

## 1. Daily Startup

```bash
# 1. Start Docker Desktop (wait for whale icon to be steady)

# 2. Start database (from project root)
docker compose -f docker/docker-compose.yml up -d

# 3. Run development server (from src/castor/)
cd src/castor
python manage.py runserver 8001

# Or just click the green Play button in PyCharm!
```

**Access the app:** http://localhost:8001  
**Access admin:** http://localhost:8001/admin/

---

## 2. Database Commands

### Docker Database Management

```bash
# Start database only
docker compose -f docker/docker-compose.yml up -d

# Start database + pgAdmin (database GUI)
docker compose -f docker/docker-compose.yml --profile tools up -d

# Stop database
docker compose -f docker/docker-compose.yml down

# Stop database + pgAdmin
docker compose -f docker/docker-compose.yml --profile tools down

# Check container status
docker ps -a

# View database logs
docker logs castor-db

# View pgAdmin logs (if running)
docker logs castor-pgadmin
```

### Django Database Commands

```bash
# From src/castor/ directory:

# Apply migrations
python manage.py migrate

# Create new migrations after model changes
python manage.py makemigrations

# Create admin account
python manage.py createsuperuser

# Reset database (careful - deletes all data!)
python manage.py flush
```

### pgAdmin Access

- **URL:** http://localhost:5050
- **Login:** admin@castor.local / admin
- **Database connection:** Host=db, Port=5432, User=castor, Password=castor

---

## 3. IFC Processing

```bash
# From src/castor/ directory:

# Parse all pending IFC files
python manage.py parse_ifc --all-pending

# Reprocess specific file (forces re-parsing)
python manage.py parse_ifc --reprocess <file_id>

# Reprocess all files
python manage.py parse_ifc --all-pending --reprocess
```

---

## 4. LLM / Ollama Commands

```bash
# Check if Ollama is running
ollama list

# Pull required models (first-time setup)
ollama pull llama3
ollama pull nomic-embed-text

# Start Ollama (if not running)
ollama serve

# Test a model
ollama run llama3 "Hello, how are you?"
```

---

## 5. Code & Dependencies

```bash
# Install/update dependencies (from project root)
uv pip install -e ".[dev]"

# Get latest code
git pull

# Check for issues
python manage.py check

# Run tests
pytest
```

---

## 6. Documentation

```bash
# From src/castor/ directory:

# Generate project context for AI assistants
python manage.py dump_context

# Output goes to: docs/PROJECT_CONTEXT.md
```

---

## 7. Troubleshooting

### "Docker is not running"
- Open Docker Desktop
- Wait for whale icon to stop animating

### "Port 8001 already in use"
```bash
# Find what's using the port (Windows)
netstat -ano | findstr :8001

# Kill the process
taskkill /PID <PID> /F
```

### "Module not found"
```bash
# Make sure venv is active (should see (.venv) in prompt)
# Reinstall dependencies
uv pip install -e ".[dev]"
```

### Database connection error
```bash
# Check if container is running
docker ps -a

# Restart the database
docker compose -f docker/docker-compose.yml down
docker compose -f docker/docker-compose.yml up -d
```

### Migrations out of sync
```bash
# From src/castor/
python manage.py migrate
```

---

## 8. URLs Quick Reference

| URL | Description |
|-----|-------------|
| http://localhost:8001 | Main app |
| http://localhost:8001/admin/ | Django Admin |
| http://localhost:5050 | pgAdmin (if started with --profile tools) |
| http://localhost:11434 | Ollama API |

---

## 9. Common Issues & Solutions

| Issue | Solution |
|-------|----------|
| Red imports in PyCharm | Right-click `src/castor` → Mark as Sources Root |
| Database not connecting | Check Docker is running, then `docker ps -a` |
| Ollama not responding | Run `ollama serve` in a separate terminal |
| Changes not showing | Restart dev server (Ctrl+C, then run again) |
| Migration errors | `python manage.py makemigrations` then `migrate` |
