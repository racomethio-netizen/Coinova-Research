# NEXUS engine image. Pinned base; non-root; deps from the lockfile only.
FROM python:3.13-slim-bookworm AS build
COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev

FROM python:3.13-slim-bookworm
RUN useradd --system --uid 10001 --no-create-home engine
WORKDIR /app
COPY --from=build --chown=engine:engine /app /app
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 HOST=0.0.0.0
USER engine
EXPOSE 8080
# `serve` for the web process; run `nexus-engine worker` for the scheduler.
CMD ["nexus-engine", "serve"]
