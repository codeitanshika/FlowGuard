# FlowGuard Code Standards

## Folder Structure Per Service

Every service under `services/<name>/` follows the same internal layout:

```
services/<name>/
├── app/
│   ├── main.py            # FastAPI app instantiation, router mounting
│   ├── api/                # route handlers, one module per resource
│   ├── core/                # settings, startup/shutdown, dependency wiring
│   ├── models/               # pydantic models (request/response schemas)
│   ├── db/                    # SQLAlchemy/asyncpg models & queries (if applicable)
│   └── services/                # business logic, kept out of route handlers
├── tests/
│   ├── unit/
│   └── integration/
├── Dockerfile
├── pyproject.toml
└── README.md                       # service-specific notes only
```

Agents under `agents/<name>/` follow a parallel but simpler layout:

```
agents/<name>/
├── agent.py             # main loop: observe → decide → act
├── actions.py             # the auditable, non-LLM action executor
├── config.py
├── tests/
└── README.md
```

## Python Conventions

- Target Python 3.12+, use built-in generics (`list[str]`, not `List[str]`).
- All I/O is `async`/`await` — no blocking calls inside FastAPI handlers or
  agent loops.
- Type-hint every function signature; no bare `Any` unless interfacing with
  an untyped third-party return value.
- Use `pydantic` models for every request/response and every inter-service
  message — never pass raw dicts across a boundary.
- One module = one responsibility. If a file mixes HTTP handling with
  business logic with DB queries, split it along `api/` / `services/` / `db/`.
- Formatting and linting are enforced by `ruff` (see `LIBRARY_DOCS.md`) — no
  manual style debates, the linter is the source of truth.

## API Design Rules

- REST resources are nouns, plural: `/transactions`, `/users`, `/scores`.
- Every mutating endpoint accepts an `Idempotency-Key` header where the
  operation has financial side effects (payments, balance updates).
- Responses use a consistent envelope: `{ "data": ..., "error": null }` on
  success, `{ "data": null, "error": { "code": ..., "message": ... } }` on
  failure.
- HTTP status codes are meaningful: `4xx` for client/validation errors,
  `5xx` only for genuine server/dependency failures — never use `200` with
  an error payload.
- Every service exposes `GET /health` (liveness) and `GET /ready`
  (readiness, checks its own dependencies).
- Inter-service calls always set a request timeout and go through the
  shared circuit breaker helper — no raw unguarded `httpx` calls to another
  service.

## Agent Code Rules

- Agents never write directly to another service's database or call a
  payment provider — they only emit control events (e.g. `circuit.trip`,
  `service.restart`) that services/infrastructure consume.
- Every LLM call (via the `anthropic` SDK) produces a structured decision
  object (pydantic model), never free text acted on directly — a
  non-LLM validator checks the decision against hard safety bounds before
  execution.
- Every agent action is logged with the triggering telemetry, the decision
  rationale, and the outcome — this log is what the dashboard's "agent
  activity" view reads from.
- Agent loops must be idempotent: re-observing the same state twice must not
  double-apply an action (e.g. re-tripping an already-open breaker is a
  no-op).
- Polling intervals and thresholds live in `config.py`, never hardcoded
  inline, so they can be tuned per environment.

## Git Commit Conventions

FlowGuard uses [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <short summary>

[optional body]
```

- **Types:** `feat`, `fix`, `refactor`, `chore`, `docs`, `test`, `perf`
- **Scope:** the service or agent name (`payment`, `fraud`, `agents`,
  `dashboard`, `shared`), or `repo` for cross-cutting changes.
- Summary is imperative mood, lowercase, no trailing period:
  `feat(payment): add idempotency key support`
- One logical change per commit — a module from `BUILD_PLAN.md` may span
  several commits, but a commit should never span multiple modules.

## Docker Standards

- Every service has its own `Dockerfile` using a multi-stage build
  (`builder` stage installs deps, final stage copies only the app + venv).
- Base image: `python:3.12-slim` for all services and agents.
- No `latest` tags in `docker-compose.yml` — pin Redis, PostgreSQL, Jaeger,
  and OTel Collector images to explicit versions.
- Every service/agent container defines a `HEALTHCHECK` (or a Compose
  `healthcheck:` block) so orchestration and the Supervisor agent can
  observe real container health, not just process uptime.
- Secrets (API keys, DB credentials) are injected via environment variables
  from `.env`, never baked into an image layer.
