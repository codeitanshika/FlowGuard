# FlowGuard — API Contracts

Contract-level definitions. Full OpenAPI specs are generated from the
actual FastAPI implementations (available at each service's `/docs` once
running) — this document is the source of truth those implementations
must match, not a replacement for it. Updated after Phase 1 to reflect
two corrections found while implementing: `PaymentRequest` dropped the
unused `provider` field (see note below), and User Service gained a
`/credit` endpoint that Phase 0 hadn't anticipated needing.

Every endpoint, on every service, additionally exposes:
- `GET /health` — liveness (process is up)
- `GET /ready` — readiness (process's own dependencies — DB, Redis — are reachable)

Every response uses a consistent envelope:
```json
{ "data": { ... }, "error": null }
{ "data": null, "error": { "code": "STRING_CODE", "message": "human readable" } }
```

Every request/response is correlated by a `trace_id`, propagated from the
Gateway (generated if not already present on the inbound request) through
every downstream call and into every event payload.

## API Gateway (external-facing)

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/auth/login` | none | issue a JWT for a test client/merchant |
| * | `/api/v1/payments/*` | JWT | proxied to Payment Service |
| * | `/api/v1/users/*` | JWT | proxied to User Service |
| * | `/api/v1/notifications/*` | JWT | proxied to Notification Service |

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

`PaymentRequest`: `{user_id, amount, currency}`
`PaymentResponse`: `{id, user_id, amount, currency, status, provider, provider_reference, failure_reason, created_at}`

`dependency` for breaker endpoints ∈ `{fraud, user, provider}`.

`provider` dropped out of `PaymentRequest` during Phase 1 implementation:
with only one configured provider (`MockPaymentProvider`) there was
nothing for a client-supplied value to select between, and accepting-but-
silently-ignoring a field is worse than not accepting it. `provider`
returns in the request once Phase 10 makes provider selection real; until
then it's server-side config, not client input — see
`services/payment/app/models/schemas.py`.

The three `/internal/circuit-breakers/*` and `/internal/recover` endpoints
above are not implemented yet — they require an actual circuit breaker to
control, which is Phase 5. Phase 1's Payment Service has no `/internal/*`
routes at all.

## Fraud Service

| Method | Path | Internal only? | Request | Response |
|---|---|---|---|---|
| POST | `/internal/risk-check` | **yes** | `{transaction_id, user_id, amount, currency}` | `RiskAssessment` |
| GET | `/risk-assessments/{transaction_id}` | no | — | `RiskAssessment` |

`RiskAssessment`: `{id, transaction_id, user_id, risk_score, risk_level, rationale, rule_version, created_at}`

## Notification Service

| Method | Path | Internal only? | Request | Response |
|---|---|---|---|---|
| GET | `/notifications?user_id=` | no | — | `NotificationRecord[]` |

No write endpoints are exposed — all writes happen via Redis event
consumption, not HTTP.

## Ops Controller (internal-only service, never routed by Gateway)

| Method | Path | Request | Response |
|---|---|---|---|
| GET | `/ops/actions` | — | list of allowlisted action names + expected params (used by Healer to build its LLM tool schema) |
| POST | `/ops/actions/open-circuit` | `{service, dependency}` | `{status, action_id}` |
| POST | `/ops/actions/reset-circuit` | `{service, dependency}` | `{status, action_id}` |
| POST | `/ops/actions/shed-traffic` | `{service, percentage}` | `{status, action_id}` |
| POST | `/ops/actions/restart-service` | `{service}` | `{status, action_id}` |

Every call requires a caller identity token scoped to `healer-agent` — no
other component holds a credential accepted by this service. Every call is
written to an append-only audit log before execution, including when
execution is rejected for not matching the allowlist.

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
