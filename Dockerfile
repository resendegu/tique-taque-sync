# ==============================================================================
# TiqueTaque Sync — Multi-Arch Dockerfile (ARM64 & AMD64)
# Publicado pelo CI em ghcr.io/resendegu/tique-taque-sync
# ==============================================================================
FROM python:3.11-slim

# Prevent Python from writing .pyc and buffering stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive

# Container defaults: listen on every interface and keep state on the volumes.
# The desktop install uses different defaults (loopback + per-user directories).
ENV HOST=0.0.0.0 \
    PORT=8000 \
    DATA_DIR=/app/data \
    TIQUETAQUE_SYNC_HOME=/app/config \
    OPEN_BROWSER_ON_START=false

WORKDIR /app

# Install runtime dependencies and curl for healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Dependências primeiro: esta camada só é reconstruída quando elas mudam.
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Instala o próprio pacote, para que o comando `tiquetaque-sync` exista dentro
# do container (ex: `docker exec tiquetaque-sync tiquetaque-sync config show`).
COPY pyproject.toml README.md ./
COPY tiquetaque_sync/ ./tiquetaque_sync/
RUN pip install --no-cache-dir --no-deps .

# Volumes de estado: banco SQLite e configuração salva pela tela /settings
RUN mkdir -p /app/data /app/config && chown -R 1000:1000 /app

USER 1000:1000

EXPOSE 8000

# Forma shell para que ${PORT} seja resolvido em tempo de execução.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f "http://localhost:${PORT:-8000}/healthz" || exit 1

# A CLI respeita HOST/PORT do ambiente — diferente de um uvicorn com porta fixa.
CMD ["tiquetaque-sync", "start", "--no-browser"]
