# syntax=docker/dockerfile:1

# Build the magi app image locally. The uv base image ships the exact Python the
# project pins (.python-version => 3.14) plus a fast, lockfile-faithful installer,
# so the build matches a local `uv sync` byte-for-byte.
FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim

# uv knobs: compile to .pyc for faster cold starts, copy out of the build cache
# (the cache mount isn't in the final layer), and never fetch a managed Python —
# the base image already has 3.14.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

# Optional dependency groups to bake in, e.g. EXTRAS="--extra s3 --extra semantic".
# Empty by default — the base install stays lean (extras are lazy-imported and
# no-op when absent; see pyproject.toml).
ARG EXTRAS=""

WORKDIR /app

# --- deps layer: resolved from the lockfile, cached until pyproject/uv.lock change.
# Bind-mount the manifests only so editing source never busts the dependency cache;
# --no-install-project installs the third-party deps without magi itself yet. ---
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project --no-dev ${EXTRAS}

# --- app layer: source + the magi package on top of the cached deps ---
COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev ${EXTRAS}

# Run as the venv interpreter directly (no `uv run` indirection at start).
ENV PATH="/app/.venv/bin:$PATH"

# Everything the app writes (sqlite db, memory markdown, local byte archive) lives
# under data/ — mount a volume over it in compose to persist across rebuilds.
RUN mkdir -p /app/data

# HTTP service by default. One entrypoint, `python main.py <channel> --docker`;
# --docker overlays only the bits that differ in a container (bind 0.0.0.0, reach
# the host llama-server). The Discord bot / admin API are alternate commands
# (see docker-compose.app.yaml).
EXPOSE 8000
CMD ["python", "main.py", "api", "--docker"]
