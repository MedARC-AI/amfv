FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1

# Install uv
# Ref: https://docs.astral.sh/uv/guides/integration/docker/#installing-uv
COPY --from=ghcr.io/astral-sh/uv:0.11.1 /uv /uvx /bin/

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Compile bytecode
# Ref: https://docs.astral.sh/uv/guides/integration/docker/#compiling-bytecode
ENV UV_COMPILE_BYTECODE=1

# uv Cache
# Ref: https://docs.astral.sh/uv/guides/integration/docker/#caching
ENV UV_LINK_MODE=copy

WORKDIR /app/

# Place executables in the environment at the front of the path
# Ref: https://docs.astral.sh/uv/guides/integration/docker/#using-the-environment
ENV PATH="/app/.venv/bin:$PATH"

# Copy the root workspace contract and member manifests before source code so
# dependency installation remains cacheable.
COPY ./pyproject.toml ./uv.lock /app/
COPY ./datasets/pyproject.toml /app/datasets/
COPY ./decomposer/pyproject.toml /app/decomposer/
COPY ./search/pyproject.toml /app/search/
COPY ./utils/pyproject.toml /app/utils/
COPY ./verifier/pyproject.toml /app/verifier/
COPY ./web/backend/pyproject.toml /app/web/backend/

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-workspace --package amfv-web-backend

COPY ./web/backend/scripts /app/web/backend/scripts
COPY ./web/backend/alembic.ini /app/web/backend/alembic.ini
COPY ./web/backend/app /app/web/backend/app

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package amfv-web-backend

WORKDIR /app/web/backend/

CMD ["fastapi", "run", "app/main.py"]
