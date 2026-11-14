# syntax=docker/dockerfile:1.7
# One image for all Python services (api, migrate, and later workers). The command
# differs per Compose service.
FROM python:3.12.10-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN groupadd --system --gid 10001 nexus \
 && useradd --system --uid 10001 --gid nexus --home-dir /app --shell /usr/sbin/nologin nexus

WORKDIR /app

# Dependencies first for layer caching.
COPY pyproject.toml ./
COPY packages ./packages
COPY services/exchange-gateway ./services/exchange-gateway
COPY apps/api ./apps/api
# Optional extra CA (e.g. a TLS-inspecting corporate proxy), passed as a BuildKit
# secret so it never ends up in an image layer. Empty/absent = public CAs only.
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -s /run/secrets/extra_ca ]; then export PIP_CERT=/run/secrets/extra_ca; fi \
 && pip install --no-compile . \
 && rm -rf /root/.cache

COPY alembic.ini ./
COPY migrations ./migrations

USER nexus:nexus
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=2).status==200 else 1)"

CMD ["uvicorn", "--factory", "nexus_api.main:create_app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*", "--no-server-header"]
