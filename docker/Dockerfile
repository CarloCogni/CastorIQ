FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install UV
RUN pip install uv

# Copy dependency files
COPY pyproject.toml .

# Install dependencies
RUN uv pip install --system -e "."

# Copy application
COPY src/castor /app/

# Collect static files
RUN python manage.py collectstatic --noinput

# Run with gunicorn
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "castor.wsgi:application"]
