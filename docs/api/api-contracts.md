# FlowGuard — API Contracts

Contract-level definitions. Full OpenAPI specs are generated from the
actual FastAPI implementations (available at each service's `/docs` once
running) — this document is the source of truth those implementations
must match, not a replacement for it. Updated after Phase 1 to reflect
two corrections found while implementing: `PaymentRequest` dropped the
unused `provider` field (see note below), and User Service gained a
`/credit` endpoint that Phase 0 hadn't anticipated needing. Updated
again after Phase 2: the Gateway's JWT auth, scope-based authorization,
and rate limiting are now real (see below), not just placeholders.
Updated again after Phase 4: `trace_id` is a real OpenTelemetry trace ID,
not an ad-hoc header FlowGuard's own code generated and forwarded.
Updated again after Phase 5: the circuit-breaker endpoints under Payment
Service are real, not placeholders. Updated again after Phase 6: every
service gained an `/internal/fault-injection` endpoint, and the Gateway
gained `/api/v1/debug/fault-inject`, which forwards to it — see
[ADR-0013](../decisions/ADR-0013-bounded-fault-injection.md).

Every endpoint, on every service, additionally exposes:
- `GET /health` — liveness (process is up)
- `GET /ready` — readiness (process's own dependencies — DB, Redis — are reachable)

Every response uses a consistent envelope:
```json
{ "data": { ... }, "error": null }
{ "data": null, "error": { "code": "STRING_CODE", "message": "human readable" } }
```

Every request/response is correlated by a `trace_id` — the active
OpenTelemetry span's real trace ID (32 hex chars), the same one Jaeger
shows for that request. Every response carries it in the `X-Trace-Id`
header for convenience (paste it into Jaeger's search box). Propagation
across HTTP hops is automatic (FastAPI/httpx auto-instrumentation reads
and writes the standard `traceparent` header); across the one non-HTTP
hop — Payment publishing an event for Notification to consume off Redis
— it's carried inside the event payload itself and extracted on the
consuming side. See [docs/UNDERSTANDING.md](../UNDERSTANDING.md)'s
OpenTelemetry section for the concrete walkthrough.

## API Gateway (external-facing)

| Method | Path | Auth | Required scope | Purpose |
|---|---|---|---|---|
| POST | `/auth/login` | none (IP rate-limited) | — | issue a JWT for a test client/merchant |
| GET/POST/PATCH | `/api/v1/payments/*` | JWT | `payments:read` / `payments:write` | proxied to Payment Service |
| GET/POST/PATCH | `/api/v1/users/*` | JWT | `users:read` / `users:write` | proxied to User Service |
| GET | `/api/v1/notifications/*` | JWT | `notifications:read` | proxied to Notification Service |
| POST/DELETE | `/api/v1/debug/fault-inject` | JWT | `debug:write` | configure/clear a bounded fault on `gateway` (in-process) or forward to `payment` / `fraud` / `user` / `notification` / `payment-provider`'s own `/internal/fault-injection` |

`POST /auth/login` — `{client_id, client_secret}` → `{access_token,
token_type: "bearer", expires_in}`. Implemented as of Phase 2:
credentials are bcrypt-hashed and configured via `GATEWAY_CLIENTS` (see
[ADR-0010](../decisions/ADR-0010-static-client-credentials.md)), rate
limited per caller IP.

Required scope is derived per-request from method (`GET`→`read`,
everything else→`write`) and the first path segment after `/api/v1/`
(see `services/gateway/app/api/authorization.py`) — a token missing the
required scope gets 403 `FORBIDDEN` even though it authenticated fine.
Every proxied request is additionally rate-limited per client
(`GATEWAY_RATE_LIMIT_PER_MINUTE`, default 100/min) — see
[docs/security/README.md](../security/README.md) and
[ADR-0009](../decisions/ADR-0009-fixed-window-rate-limiting.md).

Internal-only endpoints (`/internal/*`) on any service are **never** routed
by the Gateway — they are reachable only on the internal Docker network, by
the Ops Controller or by other services that own the relationship (e.g.
Fraud Agent → User Service freeze endpoint).

## User Service

| Method | Path | Internal only? | Request | Response |
|---|---|---|---|---|
| POST | `/users` | no | `{email, full_name, currency}` | `UserResponse` |
| GET | `/users/{id}` | no | — | `UserResponse` |
| GET | `/users/{id}/balance` | no | — | `{balance, currency}` |
| PATCH | `/internal/users/{id}/freeze` | **yes** | `{reason, source, risk_assessment_id?}` | `UserResponse` |
| PATCH | `/internal/users/{id}/unfreeze` | **yes** | `{reason, source}` | `UserResponse` |
| POST | `/internal/users/{id}/debit` | **yes** | `{amount, currency, transaction_id}` | `{balance}` or `409` on insufficient funds |
| POST | `/internal/users/{id}/credit` | **yes** | `{amount, currency, transaction_id}` | `{balance}` |
| POST | `/internal/fault-injection` | **yes** | `{mode, error_rate?, latency_ms?, duration_seconds?, component?}` | `{status, component}` |
| DELETE | `/internal/fault-injection` | **yes** | — (`?component=`) | `{status, component}` |

`UserResponse`: `{id, email, full_name, status, balance, currency, created_at}`

`/credit` didn't appear in the original Phase 0 contract — it was added
during Phase 1 implementation as the compensating action for a debit that
must be reversed (e.g. Payment Service debited the user, then the provider
declined the capture). Unlike `/debit`, it doesn't require the account to
be `active` — reversing money back to the user must succeed even on a
frozen account.

## Payment Service

| Method | Path | Internal only? | Request | Response |
|---|---|---|---|---|
| POST | `/payments` | no | `PaymentRequest` (requires `Idempotency-Key` header) | `PaymentResponse` |
| GET | `/payments/{id}` | no | — | `PaymentResponse` |
| GET | `/payments?user_id=` | no | — | `PaymentResponse[]` |
| POST | `/internal/circuit-breakers/{dependency}/open` | **yes** | — | `{dependency, state}` |
| POST | `/internal/circuit-breakers/{dependency}/reset` | **yes** | — | `{dependency, state}` |
| POST | `/internal/recover` | **yes** | — | `{status: "recovered" \| "unchanged"}` |
| POST | `/internal/fault-injection` | **yes** | `{mode, error_rate?, latency_ms?, duration_seconds?, component?}` | `{status, component}` |
| DELETE | `/internal/fault-injection` | **yes** | — (`?component=`) | `{status, component}` |

`PaymentRequest`: `{user_id, amount, currency}`
`PaymentResponse`: `{id, user_id, amount, currency, status, provider, provider_reference, failure_reason, created_at}`

`dependency` for breaker endpoints ∈ `{fraud, user, provider}`.

`component` for fault-injection endpoints defaults to `"self"` (this
service's own inbound HTTP surface, applied by `FaultInjectionMiddleware`)
and, on Payment Service only, also accepts `"provider"` — targets
`MockPaymentProvider`'s in-process `capture()` call directly, since it has
no HTTP surface of its own for middleware to sit in front of.

`provider` dropped out of `PaymentRequest` during Phase 1 implementation:
with only one configured provider (`MockPaymentProvider`) there was
nothing for a client-supplied value to select between, and accepting-but-
silently-ignoring a field is worse than not accepting it. `provider`
returns in the request once Phase 10 makes provider selection real; until
then it's server-side config, not client input — see
`services/payment/app/models/schemas.py`.

`/internal/circuit-breakers/{dependency}/open` and `/reset` are real as
of Phase 5 — `force_open()`/`reset()` on the named `CircuitBreaker`
instance (`services/payment/app/api/internal.py`), currently callable by
anyone on the internal network; Phase 8 narrows that to a token scoped
to the Healer Agent specifically, same as the Ops Controller's own
endpoints. `/internal/recover` is still not implemented — its exact
semantics (re-warm a connection pool? something else?) were never fully
pinned down even at design time, and nothing needs it yet; revisit once
the Healer Agent (Phase 8) has a concrete use for it rather than
building it speculatively now.

## Fraud Service

| Method | Path | Internal only? | Request | Response |
|---|---|---|---|---|
| POST | `/internal/risk-check` | **yes** | `{transaction_id, user_id, amount, currency}` | `RiskAssessment` |
| GET | `/risk-assessments/{transaction_id}` | no | — | `RiskAssessment` |
| POST | `/internal/fault-injection` | **yes** | `{mode, error_rate?, latency_ms?, duration_seconds?, component?}` | `{status, component}` |
| DELETE | `/internal/fault-injection` | **yes** | — (`?component=`) | `{status, component}` |

`RiskAssessment`: `{id, transaction_id, user_id, risk_score, risk_level, rationale, rule_version, created_at}`

## Notification Service

| Method | Path | Internal only? | Request | Response |
|---|---|---|---|---|
| GET | `/notifications?user_id=` | no | — | `NotificationRecord[]` |
| POST | `/internal/fault-injection` | **yes** | `{mode, error_rate?, latency_ms?, duration_seconds?, component?}` | `{status, component}` |
| DELETE | `/internal/fault-injection` | **yes** | — (`?component=`) | `{status, component}` |

No public write endpoints are exposed — notifications themselves are
created via Redis event consumption, not HTTP. The internal
fault-injection endpoints above are the one exception (Phase 6), and
they're never routed by the Gateway regardless.

## Monitor Agent (Phase 7, port 8005, never routed by Gateway)

| Method | Path | Internal only? | Request | Response |
|---|---|---|---|---|
| GET | `/health`, `/ready` | no | — | standard envelope |

The Monitor exposes no business API — it is a background loop with a health
surface. Its output is the `anomaly.detected` event (payload in
[06-event-flows.md](../architecture/06-event-flows.md); `trace_id` is an
example trace from the evaluation window) and the `anomalies` table.
`metric` ∈ `{error_rate (fraction), p95_latency (ms), throughput (req/s)}`.

## Ops Controller (Phase 8, port 8006, internal-only, never routed by Gateway)

| Method | Path | Request | Response |
|---|---|---|---|
| GET | `/ops/actions` | — | the allowlist: action names, param shape, permitted targets |
| GET | `/ops/circuits` | — | `{fraud, user, provider}` breaker states (read-only view of Payment) |
| POST | `/ops/actions/open-circuit` | `{service, dependency}` | `{status, action_id, detail}` |
| POST | `/ops/actions/reset-circuit` | `{service, dependency}` | `{status, action_id, detail}` |

`service` must be `payment`; `dependency` ∈ `{fraud, user, provider}`. Any
other value, or any extra field, is rejected (400). `shed-traffic` and
`restart-service` from the Phase 0 design are **not implemented** and have no
route (404) — see [ADR-0015](../decisions/ADR-0015-healer-proposes-guards-dispose.md).

Every request needs `Authorization: Bearer <OPS_HEALER_TOKEN>`; only the
Healer holds it (401 otherwise). An action call is written to the
`ops_actions` audit table (`received`) *before* authentication is acted
on, validation or execution, then updated to `rejected`, `executed` or
`failed` — so unauthenticated, malformed and cooldown-blocked calls are
recorded too (an unknown route 404s before any handler and is not).
The same action on the same target within 60s is rejected (429). Errors:
401 unauthenticated, 400 off-allowlist/malformed, 429 cooldown, 503 Payment
unreachable or refused.

Payment gained a read-only `GET /internal/circuit-breakers` →
`{fraud, user, provider}` (used only through the Ops Controller).

## Healer Agent (Phase 8, port 8007, never routed by Gateway)

`GET /health`, `GET /ready` only — like the Monitor it is a background loop.
Input: `anomaly.detected`. Output: `incidents` and `agent_decisions` rows,
`incident.diagnosing` and `incident.resolved` events, and Ops Controller
calls.

## Fraud Agent (Phase 9, port 8008, never routed by Gateway)

`GET /health`, `GET /ready` only — another background loop. Input:
`payment.created`. Output: `agent_decisions` rows (`high` and `borderline`
only — `low` writes nothing), a call to User Service's
`/internal/users/{id}/freeze` (`high` only, cooldown-bounded — see
[ADR-0016](../decisions/ADR-0016-fraud-agent-simulation-and-freeze-cooldown.md)),
and `fraud.user_frozen` (`high` only). A `borderline` verdict never calls
freeze, regardless of the LLM narrative's confidence — its output schema
has no field that could express a decision.

## Agents (Monitor, Healer, Fraud Agent)

Expose only `GET /health`. They have no other public API surface — their
"interface" is the Redis channels documented in
[../architecture/06-event-flows.md](../architecture/06-event-flows.md).

## Error Codes (shared vocabulary)

| Code | HTTP status | Meaning |
|---|---|---|
| `VALIDATION_ERROR` | 400 | request body failed schema validation |
| `UNAUTHORIZED` | 401 | missing/invalid JWT |
| `FORBIDDEN` | 403 | valid JWT, insufficient scope (or calling an internal-only route externally) |
| `NOT_FOUND` | 404 | resource doesn't exist |
| `CONFLICT` | 409 | e.g. insufficient balance, duplicate idempotency key with different payload |
| `RATE_LIMITED` | 429 | Gateway rate limit exceeded |
| `DEPENDENCY_UNAVAILABLE` | 503 | a required downstream call failed/breaker is open |
| `INTERNAL_ERROR` | 500 | unexpected failure |
