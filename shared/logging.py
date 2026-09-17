import logging

import structlog


def configure_logging(service_name: str, level: str = "INFO") -> None:
    """Structured JSON logging, shared by every service/agent. Pairs with
    shared/middleware.py's TraceIdMiddleware: merge_contextvars means any
    trace_id bound for the current request automatically appears on every
    log line emitted while handling it — a cheap preview of what Phase 4's
    OpenTelemetry correlation formalizes properly."""

    logging.basicConfig(format="%(message)s", level=level)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.EventRenamer("message"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(level) if isinstance(level, str) else level
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    get_logger(__name__).info("logging.configured", service=service_name)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
