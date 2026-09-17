# ADR-0006: Database-per-Service (Logical) Even on One PostgreSQL Instance

## Status
Accepted

## Context
Running one shared PostgreSQL instance for local development (Docker
Compose) is far simpler than standing up five separate database
containers. But if services are allowed to query each other's tables
directly because "it's all one database anyway," the service boundaries
defined in [03-service-boundaries.md](../architecture/03-service-boundaries.md)
become fiction — a join across schemas is a coupling nobody notices until
it breaks a deployment.

## Decision
Run one PostgreSQL instance locally, but give each service its own schema/
database (`flowguard_user`, `flowguard_payment`, `flowguard_fraud`,
`flowguard_notification`, `flowguard_control`) and its own least-privilege
DB credential that can only see its own schema. No service's SQLAlchemy
models ever reference another service's tables. Any cross-service data
need goes through that service's HTTP API.

## Consequences
- **Accepted cost:** some duplication — e.g. Payment Service stores
  `user_id` on a transaction without a foreign key into `users`, because
  that table isn't in its schema. Referential integrity for that
  relationship is enforced at the application level (User Service's API
  returning 404 for an unknown user), not the database level.
- **Benefit:** the moment this project needs to split into truly separate
  database instances (e.g. for the cloud deployment in Phase 13, or if
  User Service needs to scale its DB independently), it's a connection
  string change, not a schema redesign — the boundary was already honest.
- **Also benefits testing:** each service's test suite can be pointed at a
  throwaway schema without any risk of a test touching another service's
  data.

## Alternatives Considered
- **One shared schema, shared tables:** rejected — this is the fastest way
  to quietly turn "microservices" into a distributed monolith, where every
  service is coupled to every other service's table structure.
- **A separate PostgreSQL container per service from day one:** considered,
  but rejected for local development purely on resource/startup-time
  grounds — logical separation gives the same architectural guarantee
  without five extra containers on a laptop. Phase 13 revisits this for
  the cloud deployment specifically.
