# Build context is the repo root (docker-compose.yml sets this) so this
# stage can reach shared/, which lives outside agents/monitor/.
# Unlike the services (which run as top-level `app`), the agent keeps its
# `agents.monitor` package path, so imports match the unit tests.

FROM python:3.12-slim AS builder
WORKDIR /build
RUN apt-get update && apt-get install -y --no-install-recommends gcc \
    && rm -rf /var/lib/apt/lists/*
COPY agents/monitor/pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir --prefix=/install .

FROM python:3.12-slim
RUN useradd --create-home --uid 1000 appuser
WORKDIR /app
COPY --from=builder /install /usr/local
COPY --chown=appuser:appuser shared/ /app/shared/
COPY --chown=appuser:appuser agents/__init__.py /app/agents/__init__.py
COPY --chown=appuser:appuser agents/monitor/ /app/agents/monitor/
USER appuser
EXPOSE 8005
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
    CMD python -c "import urllib.request as r,sys; sys.exit(0 if r.urlopen('http://localhost:8005/health', timeout=2).status == 200 else 1)"
CMD ["uvicorn", "agents.monitor.main:app", "--host", "0.0.0.0", "--port", "8005"]
