FROM python:3.11-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl gosu \
    && rm -rf /var/lib/apt/lists/*

# Python deps
COPY pyproject.toml ./
RUN pip install --no-cache-dir -e ".[dev]"

# App source
COPY app/ ./app/
COPY config/ ./config/

# wiki-data volume mount point
RUN mkdir -p /wiki-data/raw/_general /wiki-data/wiki

# Non-root user for security
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app /wiki-data

# Entrypoint: fixes volume ownership at runtime, then drops to appuser
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh
ENTRYPOINT ["/docker-entrypoint.sh"]

EXPOSE 8000

# Uvicorn with reload for dev; override CMD in production
CMD ["uvicorn", "app.api.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--reload", \
     "--reload-dir", "/app/app"]