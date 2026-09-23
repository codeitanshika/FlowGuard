# Build context is the repo root (docker-compose.yml sets this) so this
# stage can reach shared/. Keeps its `agents.ops_controller` package path so
# imports match the unit tests.

FROM python:3.12-slim AS builder
WORKDIR /build
RUN apt-get update && apt-get install -y --no-install-recommends gcc \
    && rm -rf /var/lib/apt/lists/*
COPY agents/ops_controller/pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir --prefix=/install .

FROM python:3.12-slim
RUN useradd --create-home --uid 1000 appuser
WORKDIR /app
COPY --from=builder /install /usr/local
COPY --chown=appuser:appuser shared/ /app/shared/
COPY --chown=appuser:appuser agents/__init__.py /app/agents/__init__.py
COPY --chown=appuser:appuser agents/ops_controller/ /app/agents/ops_controller/
USER appuser
EXPOSE 8006
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
    CMD python -c "import urllib.request as r,sys; sys.exit(0 if r.urlopen('http://localhost:8006/health', timeout=2).status == 200 else 1)"
CMD ["uvicorn", "agents.ops_controller.main:app", "--host", "0.0.0.0", "--port", "8006"]
