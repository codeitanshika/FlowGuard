# FlowGuard — Requirements

## Functional Requirements

| ID | Requirement |
|---|---|
| FR1 | A merchant/client can create a payment transaction via a single API call. |
| FR2 | The system validates the paying user's identity and account state before processing a payment. |
| FR3 | Every transaction is evaluated for fraud risk before it is allowed to complete. |
| FR4 | The user is notified of the outcome of every transaction (success/failure). |
| FR5 | Every transaction and its state transitions are recorded durably and are queryable after the fact. |
| FR6 | Every service exposes `/health` (liveness) and `/ready` (readiness) endpoints. |
| FR7 | The system continuously observes its own telemetry and detects abnormal behavior automatically (error rate, latency, throughput). |
| FR8 | On a detected anomaly, the system uses an AI agent to diagnose the likely cause and execute a bounded, safe recovery action automatically. |
| FR9 | A failing or slow dependency is isolated so it cannot cascade into unrelated request paths. |
| FR10 | Payment creation is idempotent — retrying the same logical request (same `Idempotency-Key`) never double-charges or double-creates a transaction. |
| FR11 | Every external request is authenticated and authorized at the API Gateway before reaching a service. |
| FR12 | The system can automatically freeze a user's account when fraud risk crosses a high-risk threshold. |
| FR13 | The system integrates with a real payment provider sandbox: authentication, order creation, capture, status lookup, and webhook handling. |
| FR14 | The system supports controlled, bounded fault injection (errors, latency, timeouts) for testing resilience without uncontrolled/destructive side effects. |
| FR15 | Every AI agent decision (Monitor, Healer, Fraud Agent) is logged with its input summary, output decision, and whether it was executed — for audit. |

## Non-Functional Requirements

| ID | Requirement | Target / Notes |
|---|---|---|
| NFR1 | Availability of the critical path (payment creation) | 99.9% in the deployed environment; individual dependency failure must degrade, not outage, the whole path |
| NFR2 | Latency | p95 < 300ms for payment creation excluding provider round-trip; Gateway overhead < 20ms |
| NFR3 | Scalability | All services are stateless processes; horizontal scaling requires no code change — state lives only in Redis/PostgreSQL |
| NFR4 | Observability | 100% of requests produce a trace spanning every service they touch |
| NFR5 | Security | No secret is ever hardcoded or logged; all inter-service auth uses short-lived tokens; least-privilege DB credentials per service |
| NFR6 | Resilience | No single dependency failure is allowed to cause more than one service's requests to fail — enforced via circuit breakers |
| NFR7 | Auditability | Every state-changing action and every AI decision is persisted immutably (append-only) |
| NFR8 | Maintainability | Each service is independently deployable and independently testable; full local stack starts with one command |
| NFR9 | Testability | Every resilience behavior (circuit breaker, retry, fault injection) is covered by an automated test, not just manual demonstration |
| NFR10 | AI cost control | LLM calls never sit on the synchronous payment request path; token/cost usage is tracked per decision |
| NFR11 | Recoverability | Mean time to (automated) recovery for a detected anomaly < 2 minutes in the local/demo environment |

## Explicit Non-Goals (v1)

To keep this a credible, finishable system rather than an unbounded one:

- No PCI-DSS certification or handling of raw card PANs — sandbox tokens only.
- No multi-region / multi-tenant deployment.
- No Kubernetes — Phase 13 targets a simple single-region cloud deployment.
- No fully autonomous infrastructure changes beyond the allowlisted action set defined in [`03-service-boundaries.md`](03-service-boundaries.md) and [ADR-0005](../decisions/ADR-0005-llm-actions-via-allowlisted-executor.md).
