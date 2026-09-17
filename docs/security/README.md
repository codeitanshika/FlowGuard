# FlowGuard — Security Posture

This folder tracks security-relevant decisions and, starting in Phase 2,
the actual authn/authz implementation. As of Phase 0, no security code
exists yet — this page indexes the security-relevant *design* decisions
already made, so they're not scattered across other docs.

## Decisions Already Made (Phase 0)

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

## Planned (Phase 2)

- JWT issuance and verification at the Gateway.
- Scope-based authorization (what a given token is allowed to call).
- Rate limiting per client, backed by Redis.
- Idempotency-key enforcement on payment-mutating endpoints.
- A written explanation of authentication vs. authorization as implemented
  here, with the concrete request flow — added to this folder once built,
  not documented speculatively ahead of the code.

## Planned (Phase 12)

- Automated security checks in the CI pipeline (dependency scanning, secret
  scanning) — documented here once the workflow file exists.
