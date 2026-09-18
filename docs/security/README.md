# FlowGuard — Security Posture

## Authentication vs. Authorization

These answer two different questions, and FlowGuard enforces them as two
separate steps rather than one combined check:

- **Authentication — "who is calling?"** The Gateway verifies a bearer
  JWT's signature and expiry (`app/core/security.py`) and reads its `sub`
  claim as the calling client's identity. This step either succeeds
  (identity established) or fails outright (401 `UNAUTHORIZED`) — there's
  no partial credit for a token that's merely present but invalid or
  expired.
- **Authorization — "is *this* identity allowed to do *this*?"** A valid
  token only proves who's calling; it says nothing about what they may
  do. Every proxied request derives a required scope from its method and
  target resource (`app/api/authorization.py`: `POST /api/v1/users` →
  `users:write`) and checks it against the scopes embedded in that
  client's token. A perfectly valid, unexpired token still gets 403
  `FORBIDDEN` if it lacks the scope the route requires — see
  `readonly-client` in `.env.example`, which authenticates fine but
  cannot write.

Concretely: `get_current_client` (authentication) runs first, as a
FastAPI dependency; `check_scope` (authorization) runs after, inside the
route handler, once the target resource is known. They're different
functions in different files on purpose — conflating them tends to
produce "if authenticated then allowed" bugs.

## Implemented (Phase 2)

- **JWT issuance and verification.** `POST /auth/login` exchanges a
  `client_id`/`client_secret` pair for a short-lived HS256 JWT
  (`sub`, `scopes`, `exp`). Every proxied request requires
  `Authorization: Bearer <token>`.
- **Client credentials** are bcrypt-hashed and statically configured via
  `GATEWAY_CLIENTS` (JSON), not database-backed — see
  [ADR-0010](../decisions/ADR-0010-static-client-credentials.md).
- **Scope-based authorization** per route, as described above.
- **Rate limiting**: `FixedWindowRateLimiter` (Redis `INCR`+`EXPIRE`),
  applied per-client on every proxied request and per-IP on `/auth/login`
  specifically (so login brute-forcing is capped independently of a
  client's normal request budget) — see
  [ADR-0009](../decisions/ADR-0009-fixed-window-rate-limiting.md).
- **Idempotency-key locking.** Payment Service's idempotency handling
  (Phase 1) had a real race: two concurrent requests with the same
  `Idempotency-Key` could both pass the "already processed?" check before
  either finished. `shared/idempotency.IdempotencyLock` closes this with
  a short-TTL Redis mutex around the processing window — a racing
  request gets 409 `CONFLICT` ("retry shortly") instead of double-
  processing.
- **Request validation** at the Gateway: a write (`POST`/`PATCH`/`PUT`)
  without an `application/json` `Content-Type` is rejected (400
  `VALIDATION_ERROR`) before it's ever forwarded.
- **Secure configuration.** `jwt_secret` and `clients` have no default in
  `Settings` — a deployment missing them fails at startup instead of
  silently running unauthenticated or with an empty client list.

## Decisions From Phase 0 (Still Standing)

- **Authentication and authorization are centralized at the API Gateway.**
  No downstream service independently verifies external identity; internal
  calls use a separate, narrower internal token scoped to the specific
  caller. See [ADR-0008](../decisions/ADR-0008-gateway-centralized-authn-authz.md).
- **`/internal/*` routes are never reachable from outside the network**,
  regardless of auth — they aren't proxied by the Gateway at all. See
  [architecture/03-service-boundaries.md](../architecture/03-service-boundaries.md).
- **The Ops Controller is the only component with infrastructure-level
  credentials** (container restart, cross-service breaker control), and it
  accepts calls only from a token scoped to the Healer Agent. See
  [ADR-0005](../decisions/ADR-0005-llm-actions-via-allowlisted-executor.md).
- **No secret is ever hardcoded or logged**; all credentials load through
  `pydantic-settings` from environment variables (see
  [CODE_STANDARDS.md](../CODE_STANDARDS.md)).
- **Each service holds a least-privilege database credential**, scoped to
  its own schema only (see [ADR-0006](../decisions/ADR-0006-database-per-service.md)).

## Known Gap

Internal service-to-service calls (Payment → Fraud, Payment → User,
Fraud Agent → User's freeze endpoint once it exists) still have no
authentication of their own — they rely entirely on network isolation
(`/internal/*` never being reachable through the Gateway). A caller with
direct network access to a service's internal port can call it
unauthenticated. Acceptable for local development and the current
single-network deployment target; worth a dedicated internal-service
token (mentioned in `docs/api/api-contracts.md`'s Ops Controller section
but not yet implemented anywhere else) before this runs on shared
infrastructure with less network isolation.

## Planned (Phase 12)

- Automated security checks in the CI pipeline (dependency scanning, secret
  scanning) — documented here once the workflow file exists.
