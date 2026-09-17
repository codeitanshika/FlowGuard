# ADR-0008: Centralize Authentication/Authorization at the API Gateway

## Status
Accepted

## Context
Five services (plus internal-only agent/Ops Controller endpoints) each
need to decide whether a caller is allowed to do what it's asking. Every
service reimplementing JWT verification and scope checking independently
means five places to get it right, five places for the logic to drift, and
five attack surfaces instead of one.

## Decision
The API Gateway is the only externally reachable component. It verifies
the JWT and enforces authorization before proxying a request to a service.
Internal service-to-service and agent-to-service calls use a separate,
narrower internal auth token (scoped to the specific calling identity —
e.g. Ops Controller's calls are only accepted from a token scoped
`healer-agent`) rather than the user-facing JWT, and are only reachable on
the internal Docker network in the first place, never through the Gateway.

## Consequences
- **Accepted cost:** the Gateway becomes a single point of failure for all
  external traffic, and a bug in its auth logic is a system-wide exposure
  rather than a single-service one. This is deliberately accepted:
  concentrating the security-critical logic in one well-tested place is
  considered safer than distributing it across five services with five
  chances to get it subtly wrong.
- **Benefit:** every service downstream of the Gateway can trust that a
  request reaching it has already been authenticated — services still
  validate their own inputs and internal-only routes still check the
  internal token, but they are not each independently responsible for
  external identity verification.
- **Mitigation for the single-point-of-failure cost:** the Gateway is
  stateless (NFR3), so availability is addressed by running multiple
  replicas behind a load balancer in the cloud deployment (Phase 13), not
  by weakening the centralization.

## Alternatives Considered
- **Per-service auth (each service verifies the JWT itself):** rejected —
  duplicates security-critical logic across five codebases and makes a
  future auth change (e.g. rotating signing keys, adding a new scope) a
  five-service change instead of a one-service change.
- **A dedicated sidecar/service mesh for auth:** considered as a more
  "enterprise" pattern, but rejected as unjustified complexity for this
  project's scale — the Gateway already sits on every external request
  path, so it's the natural and sufficient place for this responsibility.
