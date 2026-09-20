from typing import Any

from opentelemetry import propagate, trace
from opentelemetry.context import Context


def current_trace_id() -> str:
    """The active span's trace ID as a 32-hex-char string, or a sentinel
    if there's no valid current span (e.g. tracing disabled/misconfigured
    — never raises, this is a logging/payload convenience field, not the
    real propagation mechanism)."""

    span_context = trace.get_current_span().get_span_context()
    if not span_context.is_valid:
        return "no-trace"
    return format(span_context.trace_id, "032x")


def inject_context(carrier: dict[str, Any]) -> dict[str, Any]:
    """Writes the current trace context (a `traceparent` key, per W3C
    Trace Context) into `carrier` in place, and returns it for chaining.
    Use this on the *publishing* side of a non-HTTP hop — HTTP already
    gets this for free from httpx/FastAPI auto-instrumentation; Redis
    pub/sub doesn't, since the message payload isn't a request the
    instrumentation libraries know how to touch."""

    propagate.inject(carrier)
    return carrier


def extract_context(carrier: dict[str, Any]) -> Context:
    """Reads a `traceparent` (if present) out of `carrier` and returns an
    OTel Context a consumer can pass as `context=` to
    `tracer.start_as_current_span(...)`, continuing the original trace
    instead of starting an unrelated one. If no valid traceparent is
    present, returns an empty context — the consumer's span just starts
    its own trace, which is the correct behavior for, e.g., a manually
    published test event with no real trace behind it."""

    return propagate.extract(carrier)
