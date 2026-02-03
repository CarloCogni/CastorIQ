# 🦫 Castor - Windows Development Setup Guide

This guide walks you through setting up the Castor development environment on Windows with PyCharm.

---

## Prerequisites Checklist

Before starting, make sure you have:

- [x] Git for Windows
- [x] Python 3.11+ 
- [x] UV package manager
- [ ] Docker Desktop for Windows
- [ ] Ollama for Windows
- [ ] PyCharm (Community or Professional)

---

## Step 1: Install Docker Desktop

1. **Download Docker Desktop** from https://www.docker.com/products/docker-desktop/

2. **Run the installer** and follow the prompts

3. **Important settings during installation:**
   - Enable WSL 2 backend (recommended)
   - Add shortcut to desktop

4. **After installation:**
   - Restart your computer if prompted
   - Launch Docker Desktop
   - Wait for it to start (the whale icon in system tray should be steady, not animated)

5. **Verify installation** - Open PowerShell and run:
   ```powershell
   docker --version
   docker compose version
   ```

---

## Step 2: Install Ollama (Native for GPU)

1. **Download Ollama** from https://ollama.ai/download

2. **Run the installer**

3. **After installation, open PowerShell and pull the required models:**
   ```powershell
   # This will take a few minutes - llama3 is ~4GB
   ollama pull llama3
   
   # Embedding model - smaller, faster
   ollama pull nomic-embed-text
   ```

4. **Verify Ollama is running:**
   ```powershell
   ollama list
   ```
   You should see both models listed.

5. **Test the models:**
   ```powershell
   ollama run llama3 "Hello, are you working?"
   ```

**Note:** Ollama runs as a background service and starts automatically with Windows. Your RTX 4070 will be used for inference automatically.

---

## Step 3: Create GitHub Repository

1. **Go to GitHub** and create a new repository:
   - Repository name: `castor`
   - Description: "Bi-Directional LLM Assistant for IFC Models and Technical Reporting"
   - Visibility: Private (can be changed later)
   - Do NOT initialize with README (we have our own)

2. **Copy the repository URL** (e.g., `https://github.com/YOUR_USERNAME/castor.git`)

---

## Step 4: Set Up Local Project

1. **Open PowerShell** and navigate to where you want the project:
   ```powershell
   cd C:\Users\YourName\Projects  # or wherever you keep projects
   ```

2. **Extract the castor-project.zip** you downloaded, or clone if you pushed to GitHub:
   ```powershell
   # If you have the zip:
   Expand-Archive castor-project.zip -DestinationPath .
   cd castor
   
   # OR if cloning from GitHub (after pushing):
   git clone https://github.com/YOUR_USERNAME/castor.git
   cd castor
   ```

3. **Initialize Git (if starting from zip):**
   ```powershell
   git init
   git add .
   git commit -m "Initial project structure"
   git branch -M main
   git remote add origin https://github.com/YOUR_USERNAME/castor.git
   git push -u origin main
   ```

---

## Step 5: Start PostgreSQL with Docker

1. **Make sure Docker Desktop is running** (check the system tray)

2. **Open PowerShell in the project directory** and start the database:
   ```powershell
   cd castor  # if not already there
   docker compose -f docker/docker-compose.yml up -d
   ```

3. **Verify the database is running:**
   ```powershell
   docker ps
   ```
   You should see `castor-db` running.

4. **Check the logs if needed:**
   ```powershell
   docker logs castor-db
   ```

**Database details:**
- Host: `localhost`
- Port: `5432`
- Database: `castor`
- Username: `castor`
- Password: `castor`

---

## Step 6: Set Up Python Environment with UV

1. **Open PowerShell in the project root:**
   ```powershell
   cd castor
   ```

2. **Create virtual environment:**
   ```powershell
   uv venv
   ```

3. **Activate the virtual environment:**
   ```powershell
   .venv\Scripts\activate
   ```
   Your prompt should now show `(.venv)` at the beginning.

4. **Install dependencies:**
   ```powershell
   uv pip install -e ".[dev]"
   ```

5. **Verify installation:**
   ```powershell
   python -c "import django; print(django.VERSION)"
   ```

---

## Step 7: Configure Environment Variables

1. **Copy the example environment file:**
   ```powershell
   copy .env.example .env
   ```

2. **Edit `.env`** and update if needed (defaults should work for local development):
   ```
   # Django Settings
   DJANGO_SECRET_KEY=your-secret-key-change-me
   DJANGO_DEBUG=True
   
   # Database (matches docker-compose.yml)
   DATABASE_URL=postgresql://castor:castor@localhost:5432/castor
   
   # Ollama (running natively)
   OLLAMA_HOST=http://localhost:11434
   OLLAMA_MODEL=llama3
   OLLAMA_EMBED_MODEL=nomic-embed-text
   ```

---

## Step 8: Initialize Django

1. **Navigate to the Django project:**
   ```powershell
   cd src/castor
   ```

2. **Run migrations:**
   ```powershell
   python manage.py migrate
   ```

3. **Create a superuser:**
   ```powershell
   python manage.py createsuperuser
   ```
   Follow the prompts to create an admin account.

4. **Test the server:**
   ```powershell
   python manage.py runserver
   ```

5. **Open your browser** and visit:
   - http://localhost:8000/api/health/ - Should show `{"status": "healthy"}`
   - http://localhost:8000/admin/ - Django admin (login with superuser)

---

## Step 9: Configure PyCharm

1. **Open PyCharm** and select "Open" → navigate to the `castor` folder

2. **Configure Python Interpreter:**
   - Go to `File` → `Settings` → `Project: castor` → `Python Interpreter`
   - Click the gear icon → `Add`
   - Select "Existing environment"
   - Browse to: `C:\Users\YourName\Projects\castor\.venv\Scripts\python.exe`
   - Click OK

3. **Configure Django Support (Professional only):**
   - Go to `File` → `Settings` → `Languages & Frameworks` → `Django`
   - Enable Django support
   - Django project root: `src/castor`
   - Settings: `castor/settings/local.py`
   - Manage script: `manage.py`

4. **Set up Run Configuration:**
   - Click `Run` → `Edit Configurations`
   - Click `+` → `Django Server`
   - Name: `Castor Dev Server`
   - Host: `localhost`
   - Port: `8000`
   - Environment variables: Add `DJANGO_SETTINGS_MODULE=castor.settings.local`
   - Working directory: `C:\Users\YourName\Projects\castor\src\castor`

5. **For PyCharm Community Edition:**
   - Click `Run` → `Edit Configurations`
   - Click `+` → `Python`
   - Name: `Castor Dev Server`
   - Script path: `C:\Users\YourName\Projects\castor\src\castor\manage.py`
   - Parameters: `runserver`
   - Working directory: `C:\Users\YourName\Projects\castor\src\castor`
   - Environment variables: `DJANGO_SETTINGS_MODULE=castor.settings.local`

---

## Step 10: Verify Everything Works

Run this checklist to make sure everything is set up correctly:

### 1. Docker is running
```powershell
docker ps
# Should show castor-db
```

### 2. Database is accessible
```powershell
cd src/castor
python manage.py dbshell
# Type \q to exit
```

### 3. Ollama is running
```powershell
ollama list
# Should show llama3 and nomic-embed-text
```

### 4. Django server starts
```powershell
python manage.py runserver
# Should start without errors
```

### 5. Tests pass
```powershell
cd ../..  # back to project root
pytest
# Should show 1 passed test
```

---

## Common Commands

```powershell
# Start database
docker compose -f docker/docker-compose.yml up -d

# Stop database
docker compose -f docker/docker-compose.yml down

# Reset database (deletes all data)
docker compose -f docker/docker-compose.yml down -v
docker compose -f docker/docker-compose.yml up -d
cd src/castor
python manage.py migrate

# Run Django server
cd src/castor
python manage.py runserver

# Run tests
pytest

# Create new migration after model changes
python manage.py makemigrations

# Apply migrations
python manage.py migrate

# Open Django shell
python manage.py shell

# Format code
ruff format .

# Check code
ruff check .
```

---

## Project Structure Reference

```
castor/
├── docker/
│   ├── docker-compose.yml      # Local dev (PostgreSQL only)
│   ├── docker-compose.prod.yml # Production (everything)
│   ├── Dockerfile              # Django container
│   └── init-db.sql             # pgvector extension
├── src/castor/
│   ├── manage.py               # Django CLI
│   ├── castor/                 # Django project settings
│   │   ├── settings/
│   │   │   ├── base.py         # Shared settings
│   │   │   ├── local.py        # Local dev
│   │   │   └── production.py   # Production
│   │   ├── urls.py
│   │   └── wsgi.py
│   ├── core/                   # Shared utilities
│   ├── environments/           # Workspace management
│   ├── chat/                   # Chat sessions
│   ├── ifc_processor/          # IFC parsing
│   ├── documents/              # Document processing
│   ├── embeddings/             # RAG pipeline
│   └── writeback/              # IFC modifications
├── tests/
├── docs/
├── .env                        # Your local config (git ignored)
├── .env.example                # Template
├── pyproject.toml              # Dependencies
└── README.md
```

---

## Troubleshooting

### "Docker is not running"
- Open Docker Desktop and wait for it to fully start
- Check the system tray for the Docker whale icon

### "psycopg cannot connect"
- Make sure Docker is running
- Run `docker ps` to verify castor-db is up
- Check if port 5432 is already in use: `netstat -ano | findstr :5432`

### "Ollama connection refused"
- Make sure Ollama is running (check system tray)
- Try restarting: `ollama serve` in a new terminal

### "Module not found" errors
- Make sure your virtual environment is activated: `.venv\Scripts\activate`
- Reinstall dependencies: `uv pip install -e ".[dev]"`

### PyCharm doesn't recognize imports
- Refresh the Python interpreter
- Mark `src/castor` as Sources Root (right-click → Mark Directory as → Sources Root)

---

## Next Steps

Once everything is set up:

1. **Explore the models** in each app to understand the data structure
2. **Run the admin** at `/admin/` to see the data models
3. **Start with the IFC processor** - implement basic parsing with IfcOpenShell
4. **Add a simple API endpoint** to upload an IFC file

Happy coding! 🦫
