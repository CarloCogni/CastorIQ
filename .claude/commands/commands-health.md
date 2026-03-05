Check the health of the Castor development environment.

1. Check Docker is running: docker compose -f docker/docker-compose.yml ps
2. Check Ollama is serving: curl -s http://localhost:11434/api/tags
3. Check database connection: cd src and uv run manage.py check --database default
4. Check for pending migrations: cd src and uv run manage.py showmigrations --list | grep "\[ \]"
5. Run linter: cd src and ruff check . --statistics
6. Report status of each component and any issues found