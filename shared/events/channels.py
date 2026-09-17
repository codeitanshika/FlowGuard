class Channels:
    """Redis pub/sub channel names — must match docs/architecture/06-event-flows.md
    exactly, since that doc is the contract for what payload shape each one
    carries."""

    PAYMENT_CREATED = "payment.created"
    PAYMENT_COMPLETED = "payment.completed"
    PAYMENT_FAILED = "payment.failed"
    FRAUD_USER_FROZEN = "fraud.user_frozen"
    ANOMALY_DETECTED = "anomaly.detected"
    INCIDENT_DIAGNOSING = "incident.diagnosing"
    INCIDENT_RESOLVED = "incident.resolved"
    CIRCUIT_BREAKER_STATE_CHANGED = "circuitbreaker.state_changed"
