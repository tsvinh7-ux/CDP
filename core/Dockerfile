# syntax=docker/dockerfile:1
# Build from the repository root:  docker build -f core/Dockerfile -t dmag/cdp-core .
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    CDP_SDK_PATH=/app/sdk/web/cdp.js
WORKDIR /app

COPY core/pyproject.toml core/pyproject.toml
COPY core/cdp_core core/cdp_core
# Optional: behind a TLS-inspecting proxy, pass its CA with
#   docker build --secret id=pip_ca,src=/path/ca.crt ...
RUN --mount=type=secret,id=pip_ca,required=false \
    if [ -f /run/secrets/pip_ca ]; then export PIP_CERT=/run/secrets/pip_ca; fi; \
    pip install "./core[kafka]" && useradd --system --uid 10001 cdp
COPY sdk/web/cdp.js sdk/web/cdp.js

USER cdp
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"
CMD ["uvicorn", "cdp_core.app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--workers", "2"]
