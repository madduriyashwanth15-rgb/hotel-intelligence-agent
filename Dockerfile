# Hotel Intelligence Agent — application image (local demo; ADR-0027).
#
# Contains the API, CLIs, migrations, the synthetic knowledge base and the pinned embedding
# model (downloaded once at build time and verified against its SHA-256). No LLM, no
# secrets: database credentials come from the environment at run time.

ARG PYTHON_IMAGE=python:3.12-slim-bookworm

FROM ghcr.io/astral-sh/uv:0.11.3 AS uv

# ---------------------------------------------------------------------------- build
FROM ${PYTHON_IMAGE} AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/local/bin/python3.12
WORKDIR /app

# Dependencies first (cached layer), exactly as locked; no dev tools in the image.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev

# Embedding model (bge-small-en-v1.5, ~65 MB): pinned revision + SHA-256 check. Settings
# need *some* database credentials to load; these placeholders are never used to connect.
RUN POSTGRES_USER=unused POSTGRES_PASSWORD=unused /app/.venv/bin/hotel-kb download-model

# ---------------------------------------------------------------------------- runtime
FROM ${PYTHON_IMAGE}
RUN groupadd --system app && useradd --system --gid app --home-dir /app app
WORKDIR /app
COPY --from=build --chown=root:root /app/.venv /app/.venv
COPY --from=build --chown=root:root /app/.models /app/.models
COPY --chown=root:root src ./src
COPY --chown=root:root migrations ./migrations
COPY --chown=root:root alembic.ini pyproject.toml ./
COPY --chown=root:root data ./data
COPY --chown=root:root eval ./eval
COPY --chown=root:root docker/entrypoint.sh /usr/local/bin/hotel-demo-entrypoint
RUN chmod 0755 /usr/local/bin/hotel-demo-entrypoint

ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    APP_ENV=development \
    LLM_PROVIDER=deterministic

# Code and model are root-owned and read-only for the service user.
USER app
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=5s --start-period=60s --retries=12 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=4).status == 200 else 1)"]
ENTRYPOINT ["hotel-demo-entrypoint"]
