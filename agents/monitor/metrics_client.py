import time
from typing import Any

import httpx

from agents.monitor.detector import SpanSample
from shared.logging import get_logger

logger = get_logger(__name__)

# Control-plane and probe traffic, not user traffic: health checks run
# every few seconds on every service and would swamp throughput and dilute
# error rate; fault-injection/breaker control calls are operator actions.
# (Fraud's /internal/risk-check is real payment-path traffic, so "/internal"
# as a whole is deliberately NOT ignored.)
IGNORED_PATH_PREFIXES = (
    "/health",
    "/ready",
    "/internal/fault-injection",
    "/internal/circuit-breakers",
    "/api/v1/debug",
)

_PATH_TAGS = ("http.route", "http.target", "url.path")


class MetricsUnavailableError(Exception):
    pass


def _tags(span: dict[str, Any]) -> dict[str, Any]:
    return {t["key"]: t.get("value") for t in span.get("tags", [])}


def _request_path(span: dict[str, Any], tags: dict[str, Any]) -> str:
    for key in _PATH_TAGS:
        value = tags.get(key)
        if isinstance(value, str) and value:
            return value
    # FastAPI names server spans "<METHOD> <route>".
    parts = str(span.get("operationName", "")).split(" ", 1)
    return parts[1] if len(parts) == 2 else ""


def _is_error(tags: dict[str, Any]) -> bool:
    if tags.get("error") in (True, "true"):
        return True
    if tags.get("otel.status_code") == "ERROR":
        return True
    for key in ("http.status_code", "http.response.status_code"):
        status = tags.get(key)
        if status is not None and int(status) >= 500:
            return True
    return False


def parse_traces(payload: dict[str, Any], service: str) -> list[SpanSample]:
    """Reduces a Jaeger /api/traces response to one SpanSample per inbound
    request handled by `service`. A trace contains spans from every service
    it crossed, so this keeps only spans whose process is `service` and
    whose kind is server — the request as this service experienced it,
    not its internal or outbound sub-spans."""

    samples: list[SpanSample] = []
    for trace in payload.get("data") or []:
        processes = trace.get("processes", {})
        for span in trace.get("spans", []):
            process = processes.get(span.get("processID"), {})
            if process.get("serviceName") != service:
                continue
            tags = _tags(span)
            if tags.get("span.kind") != "server":
                continue
            if _request_path(span, tags).startswith(IGNORED_PATH_PREFIXES):
                continue
            samples.append(
                SpanSample(
                    trace_id=span.get("traceID") or trace.get("traceID", ""),
                    start_us=int(span.get("startTime", 0)),
                    duration_ms=int(span.get("duration", 0)) / 1000,
                    is_error=_is_error(tags),
                )
            )
    return samples


class JaegerMetricsClient:
    """Reads recent spans from Jaeger's query API (the one its own UI
    uses). See ADR-0014 for why Jaeger and not a metrics backend, and for
    what that costs: the API caps traces per query, so the caller is told
    when a result hit the cap."""

    def __init__(self, base_url: str, max_traces: int, http_client: httpx.AsyncClient) -> None:
        self._base_url = base_url.rstrip("/")
        self._max_traces = max_traces
        self._http = http_client

    async def fetch_samples(self, service: str, window_seconds: int) -> tuple[list[SpanSample], bool]:
        end_us = int(time.time() * 1_000_000)
        start_us = end_us - window_seconds * 1_000_000
        try:
            response = await self._http.get(
                f"{self._base_url}/api/traces",
                params={"service": service, "start": start_us, "end": end_us, "limit": self._max_traces},
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise MetricsUnavailableError(f"jaeger query failed for '{service}': {exc}") from exc

        truncated = len(payload.get("data") or []) >= self._max_traces
        samples = [s for s in parse_traces(payload, service) if s.start_us >= start_us]
        return samples, truncated
