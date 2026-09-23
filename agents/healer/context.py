import re
from dataclasses import asdict, dataclass
from typing import Any

import httpx

from shared.logging import get_logger

logger = get_logger(__name__)

_MAX_SPANS = 30
_MAX_TEXT = 200
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]+")

# What a span's error text says about which dependency failed. The wording
# comes from Payment's clients and breaker ("user service unreachable",
# "payment provider fault injected", "circuit 'payment:fraud' is open").
_DEPENDENCY_PATTERNS = {
    "provider": re.compile(r"\bprovider\b|payment:provider", re.IGNORECASE),
    "user": re.compile(r"\buser service\b|payment:user", re.IGNORECASE),
    "fraud": re.compile(r"\bfraud service\b|payment:fraud", re.IGNORECASE),
}


@dataclass(frozen=True)
class SpanFact:
    service: str
    operation: str
    is_error: bool
    http_status: int | None
    failure_kind: str | None
    description: str | None


def clean_text(value: Any, limit: int = _MAX_TEXT) -> str:
    """Telemetry strings can carry attacker-influenced content (URLs,
    exception text). Strip control characters and bound the length before
    they go anywhere near a prompt. This is hygiene, not the defence: the
    defence is that nothing the model says can exceed the allowlist."""
    text = _CONTROL_CHARS.sub(" ", str(value)).strip()
    return text[:limit]


def summarize_trace(payload: dict[str, Any]) -> list[SpanFact]:
    facts: list[SpanFact] = []
    for trace in payload.get("data") or []:
        processes = trace.get("processes", {})
        for span in trace.get("spans", []):
            tags = {t["key"]: t.get("value") for t in span.get("tags", [])}
            if tags.get("span.kind") != "server":
                continue
            is_error = tags.get("error") in (True, "true") or tags.get("otel.status_code") == "ERROR"
            status = tags.get("http.status_code") or tags.get("http.response.status_code")
            description = tags.get("otel.status_description")
            facts.append(
                SpanFact(
                    service=clean_text(processes.get(span.get("processID"), {}).get("serviceName", "?"), 40),
                    operation=clean_text(span.get("operationName", ""), 80),
                    is_error=is_error,
                    http_status=int(status) if status is not None else None,
                    failure_kind=clean_text(tags["flowguard.failure_kind"], 40)
                    if tags.get("flowguard.failure_kind")
                    else None,
                    description=clean_text(description) if description else None,
                )
            )
    return facts[:_MAX_SPANS]


def implicated_dependencies(facts: list[SpanFact], service: str) -> set[str]:
    """Dependencies named in the error text of `service`'s own failing
    spans. Empty when there is no evidence; more than one when the
    evidence is ambiguous — callers must treat both as "don't guess"."""
    found: set[str] = set()
    for fact in facts:
        if fact.service != service or not fact.is_error or not fact.description:
            continue
        for dependency, pattern in _DEPENDENCY_PATTERNS.items():
            if pattern.search(fact.description):
                found.add(dependency)
    return found


def facts_as_dicts(facts: list[SpanFact]) -> list[dict[str, Any]]:
    return [asdict(f) for f in facts]


class ContextGatherer:
    """Best-effort evidence collection: a missing trace or unreachable
    Ops Controller degrades the diagnosis (less to go on), it never
    blocks or crashes the incident."""

    def __init__(self, jaeger_query_url: str, http_client: httpx.AsyncClient, ops_client) -> None:
        self._jaeger = jaeger_query_url.rstrip("/")
        self._http = http_client
        self._ops = ops_client

    async def trace_facts(self, trace_id: str | None) -> list[SpanFact]:
        if not trace_id:
            return []
        try:
            response = await self._http.get(f"{self._jaeger}/api/traces/{trace_id}")
            response.raise_for_status()
            return summarize_trace(response.json())
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("healer.trace_unavailable", trace_id=trace_id, error=str(exc))
            return []

    async def circuits(self) -> dict[str, str] | None:
        try:
            return await self._ops.circuits()
        except Exception as exc:  # noqa: BLE001 - context is best-effort
            logger.warning("healer.circuit_state_unavailable", error=str(exc))
            return None
