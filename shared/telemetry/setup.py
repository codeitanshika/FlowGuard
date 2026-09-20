import os

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

# Standard OTel env var, read directly rather than through our own
# pydantic-settings Settings classes — this is a recognized convention
# other tooling expects to find as-is, not a FlowGuard-specific setting.
# If unreachable, the exporter fails quietly in the background (retries,
# logs) rather than blocking the app — fine for local dev without Jaeger
# up, and the right failure mode generally: telemetry going missing
# should never take the payment path down with it.
_DEFAULT_OTLP_ENDPOINT = "http://localhost:4318"

# configure_tracing/instrument_fastapi are unconditional top-level imports
# above because every service is a FastAPI app exporting via OTLP/HTTP —
# every service's pyproject.toml declares those two packages. httpx/
# sqlalchemy/redis are each used by only some services (see
# docs/CODE_STANDARDS.md's per-service dependency matrix — no point
# instrumenting a client a service never imports), so those imports live
# inside their own function below instead of up here: importing them
# unconditionally at module level broke every service that doesn't
# declare that one instrumentor's package, since shared/ code runs
# inside every container regardless of which subset of these functions
# that service actually calls.


def configure_tracing(service_name: str) -> None:
    """Call once at startup, before creating the FastAPI app. Sets up a
    global TracerProvider exporting to Jaeger's OTLP/HTTP receiver — see
    ADR-0011 for why services send there directly instead of through a
    separate OTel Collector."""

    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", _DEFAULT_OTLP_ENDPOINT)
    resource = Resource.create({SERVICE_NAME: service_name})
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(endpoint=f"{endpoint.rstrip('/')}/v1/traces")
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)


def instrument_fastapi(app) -> None:
    FastAPIInstrumentor.instrument_app(app)


def instrument_httpx() -> None:
    """Instruments every httpx client process-wide (Payment/Gateway create
    a fresh httpx.AsyncClient per outbound call — that's fine, this
    patches the underlying transport once, not per-instance). Only
    services that actually make outbound HTTP calls (Payment, Gateway)
    declare opentelemetry-instrumentation-httpx and call this."""
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

    HTTPXClientInstrumentor().instrument()


def instrument_sqlalchemy(engine) -> None:
    """`engine.sync_engine` because SQLAlchemy's own instrumentation hooks
    into the sync engine's event system even when the app uses the async
    engine — asyncpg's async engine bridges to it via greenlet."""
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

    SQLAlchemyInstrumentor().instrument(engine=engine.sync_engine)


def instrument_redis() -> None:
    from opentelemetry.instrumentation.redis import RedisInstrumentor

    RedisInstrumentor().instrument()
