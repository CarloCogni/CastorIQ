# 🦫 Castor — Final Setup Guide

**You're almost there!** Follow these steps to get Castor running on your machine.

**You should already have:**
- ✅ Software installed (PyCharm, Docker Desktop, Git, Ollama, Python, UV)
- ✅ Repository cloned and opened in PyCharm
- ✅ `uv sync` completed (virtual environment created)
- ✅ Ollama models downloaded (`llama3.1:8b` and `mxbai-embed-large`)

> **All commands below are run in PyCharm's Terminal** (click the "Terminal" tab at the bottom of PyCharm). Make sure you are in the project root folder (`castor`).

---

## Step 1: Create Your `.env` File

The app needs a configuration file called `.env`. We'll create it by copying the provided example:

**Windows:**
```
copy .env.example .env
```

**Mac/Linux:**
```
cp .env.example .env
```

> That's it — the default values work for local development. No need to edit anything.

---

## Step 2: Start Docker (Database)

> ⚠️ **First, make sure Docker Desktop is open and fully started.** Open it and wait until the whale icon in your system tray (bottom-right) stops animating.

Then run:

```
docker compose -f docker/docker-compose.yml up -d
```

You should see something like `Container castor-db Started`. ✅

---

## Step 3: Start Ollama

Ollama needs to be running in the background for the AI features to work.

Open a **separate** Command Prompt window (Windows key → type `cmd` → Enter) and run:

```
ollama serve
```

> **Leave this window open** — don't close it while you're working with Castor.

---

## Step 4: Set Up the Database

Back in **PyCharm's Terminal**, run:

```
uv run src/manage.py migrate
```

You should see a series of `Applying ... OK` messages. This creates all the database tables. ✅

---

## Step 5: Create Your Admin Account

```
uv run src/manage.py createsuperuser
```

It will ask you for:
- **Username** — pick whatever you want
- **Email** — can be anything (e.g. your email)
- **Password** — type it twice (it won't show characters as you type — that's normal!)

---

## Step 6: Run the Server! 🚀

```
uv run src/manage.py runserver
```

You should see:

```
Starting development server at http://127.0.0.1:8000/
```

**Open your browser and go to:** [http://localhost:8000](http://localhost:8000)

**Admin panel:** [http://localhost:8000/admin/](http://localhost:8000/admin/) — log in with the account you just created.

### 🎉 Done! Castor is running on your machine.

---

## Quick Reference — Daily Startup

Every time you want to work on Castor:

1. **Open Docker Desktop** → wait for it to fully start
2. **Start Ollama** → open a Command Prompt and run `ollama serve`
3. **Open PyCharm** → open the Castor project
4. **In PyCharm's Terminal**, run these commands one by one:
   ```
   docker compose -f docker/docker-compose.yml up -d   (or activate directly from docker APP)
   uv run src/manage.py runserver
   ```
5. **Open** [http://localhost:8000](http://localhost:8000) in your browser

### When Carlo Pushes Updates

```
git pull
uv sync
uv run src/manage.py migrate
uv run src/manage.py runserver
```

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| **"Docker is not running"** | Open Docker Desktop, wait for whale icon to stop animating |
| **"Module not found"** | Run `uv sync` from the project root folder |
| **"Port already in use"** | Something else is using port 8000. Try: `uv run src/manage.py runserver 8001` |
| **Database errors after `git pull`** | Run `uv run src/manage.py migrate` |
| **Ollama/AI errors** | Make sure `ollama serve` is running in a separate terminal |
| **`git pull` shows conflicts** | Contact Carlo |

**Stuck? Screenshot the error and send it to Carlo!** 📸
