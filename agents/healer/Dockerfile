# Build context is the repo root (docker-compose.yml sets this). The Healer
# reuses two small pieces of other agents rather than duplicating them: the
# Monitor's detection code (so "recovered" means exactly "the Monitor would
# not alert") and the Ops Controller's allowlist definition (one source of
# truth for what actions exist). Only those files are copied, not the
# other agents' services.

FROM python:3.12-slim AS builder
WORKDIR /build
RUN apt-get update && apt-get install -y --no-install-recommends gcc \
    && rm -rf /var/lib/apt/lists/*
COPY agents/healer/pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir --prefix=/install .

FROM python:3.12-slim
RUN useradd --create-home --uid 1000 appuser
WORKDIR /app
COPY --from=builder /install /usr/local
COPY --chown=appuser:appuser shared/ /app/shared/
COPY --chown=appuser:appuser agents/__init__.py /app/agents/__init__.py
COPY --chown=appuser:appuser agents/healer/ /app/agents/healer/
COPY --chown=appuser:appuser agents/monitor/__init__.py agents/monitor/detector.py agents/monitor/metrics_client.py agents/monitor/thresholds.py /app/agents/monitor/
COPY --chown=appuser:appuser agents/ops_controller/__init__.py agents/ops_controller/allowlist.py /app/agents/ops_controller/
USER appuser
EXPOSE 8007
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
    CMD python -c "import urllib.request as r,sys; sys.exit(0 if r.urlopen('http://localhost:8007/health', timeout=2).status == 200 else 1)"
CMD ["uvicorn", "agents.healer.main:app", "--host", "0.0.0.0", "--port", "8007"]
