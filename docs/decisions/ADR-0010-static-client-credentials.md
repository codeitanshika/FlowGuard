# ADR-0010: API Client Credentials Are Static Config, Not a Database

## Status
Accepted

## Context
The Gateway needs to authenticate *callers* — merchants/test clients
hitting the API — before issuing a JWT. The conventional answer is a
`clients` table: a CRUD API, a database, self-service credential
rotation. FlowGuard's actual number of API consumers at this stage is
small and known in advance (a handful of test/demo clients), and
[ADR-0008](ADR-0008-gateway-centralized-authn-authz.md) already commits
the Gateway to being stateless — no database, no persistence of its own.

## Decision
Client credentials (`client_id`, a bcrypt hash of the secret, and a
scope list) are configured via the `GATEWAY_CLIENTS` environment
variable — a small JSON map, parsed by `pydantic-settings` straight into
typed `ClientCredential` objects at startup. Rotating or adding a client
means editing config and redeploying, not calling an API.

## Consequences
- **Accepted cost:** no self-service client management, no runtime
  credential rotation without a restart, and the client list is visible
  to anyone who can read the Gateway's environment — acceptable for a
  small, operator-controlled set of API consumers, not acceptable if
  this ever needs to support external self-signup.
- **Benefit:** the Gateway stays genuinely stateless (still no database,
  consistent with ADR-0008/NFR3), there's no `clients` table whose
  compromise would leak every credential at once, and there's nothing
  to migrate or back up for this concern.
- **Secrets are still bcrypt-hashed in config**, not plaintext — config-
  based doesn't mean credentials are stored in the clear; only the hash
  lives in `GATEWAY_CLIENTS`.

## Alternatives Considered
- **A `clients` table with a self-service registration API:** rejected
  for this project's actual scale — it would reintroduce the database
  dependency ADR-0008 deliberately avoided, for a self-service capability
  nothing in this project's scope currently needs.
- **Hardcoding credentials in source:** rejected outright — this is
  exactly what environment-based config exists to avoid (see
  CODE_STANDARDS.md: no hardcoded secrets).

## Revisit Trigger
If FlowGuard ever needs external, self-registering API consumers (not
just a fixed, operator-managed set), that's the point to introduce a
real client-management service and database — not before.
