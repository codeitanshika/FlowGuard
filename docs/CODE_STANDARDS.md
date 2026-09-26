# FlowGuard Code Standards

Supersedes the old `CODE_STANDARDS.md` (now in
[archive/](archive/CODE_STANDARDS.md)), updated for the Gateway, the
Monitor/Healer/Fraud Agent split, and the Ops Controller.

## Monorepo Folder Structure

```
flowguard/
├── services/
│   ├── gateway/
│   ├── payment/
│   ├── fraud/
│   ├── user/
│   └── notification/
├── agents/
│   ├── monitor/
│   ├── healer/
│   ├── fraud/
│   └── ops_controller/      # allowlisted action executor, see ADR-0005
│                            # (lives here, not infra/ — it's a service like the others,
│                            # just one with no business API; see ADR-0015)
├── infra/
│   └── docker/
├── shared/
│   ├── events/
│   ├── control_plane/       # shared Monitor/Healer/Ops Controller/Fraud Agent tables
│   ├── schemas/
│   ├── telemetry/
│   └── config/
├── tests/                   # real as of Phase 11 — see ADR-0018
│   ├── unit/                     # no infra needed; the default for bare `pytest`
│   ├── integration/              # needs `docker compose up`
│   └── chaos/                    # needs `docker compose up` + the test compose override
├── docs/
├── docker-compose.yml
├── .env.example
└── .github/workflows/
```

One exception not shown above: `services/payment/tests/` is its own
pytest run (`app` is a package name every service shares, so Payment's
PayPal-client tests can't share a session with the root `tests/`).

## Folder Structure Per Service

Every service under `services/<name>/` follows the same internal layout
(see [architecture/04-lld.md](architecture/04-lld.md) for the exact
module contents per service):

```
services/<name>/
├── app/
│   ├── api/          # route handlers — one module per resource, internal/* routes clearly separated
│   ├── core/           # settings, startup/shutdown, dependency wiring
│   ├── models/            # pydantic request/response schemas
│   ├── db/                  # SQLAlchemy models & queries, scoped to this service's own schema only
│   ├── clients/                # breaker-wrapped httpx clients for outbound calls
│   └── services/                  # business logic — never inline in route handlers
├── tests/
│   ├── unit/
│   └── integration/
├── Dockerfile
├── pyproject.toml
└── README.md
```

Agents under `agents/<name>/` follow the parallel layout in
[architecture/04-lld.md](architecture/04-lld.md): `agent.py` (main loop),
a schema module for structured LLM output, and a `db.py` for the shared
control-plane tables — never a direct write to a business service's schema.

## Python Conventions

- Target Python 3.12+, use built-in generics (`list[str]`, not `List[str]`).
- All I/O is `async`/`await` — no blocking calls inside FastAPI handlers or
  agent loops.
- Type-hint every function signature; no bare `Any` unless interfacing with
  an untyped third-party return value.
- Use Pydantic v2 models for every request/response and every inter-service
  or event message — never pass raw dicts across a boundary.
- No bare `except:` — catch specific exceptions and handle them explicitly.
- One module = one responsibility (`api/` vs `services/` vs `db/`).
- Formatting and linting are `ruff` alone (`ruff check`, `ruff format`; one
  `ruff.toml`) — enforced in CI, so no manual style debates. See
  [ADR-0019](decisions/ADR-0019-cicd-build-once-promote-the-artifact.md).
  Suppress a rule only with a written reason (`# nosec B311` says why; the
  ignores in `ruff.toml` say why).
- Tests that use random ids must not depend on any *probabilistic* signal
  staying quiet (the simulated geo check fires for ~1 in 10 UUIDs): pin it,
  or the test flakes and CI becomes noise.

## API Design Rules

- REST resources are nouns, plural: `/payments`, `/users`, `/notifications`.
- Every mutating endpoint with financial side effects requires an
  `Idempotency-Key` header (see [api/api-contracts.md](api/api-contracts.md)).
- Responses use the shared envelope: `{ "data": ..., "error": null }` on
  success, `{ "data": null, "error": { "code", "message" } }` on failure —
  error codes are the shared vocabulary in `api-contracts.md`.
- `4xx` for client/validation errors, `5xx` only for genuine server/
  dependency failures.
- Every service exposes `GET /health` and `GET /ready`.
- `/internal/*` routes are never proxied by the Gateway — reachable only on
  the internal network, and only by the caller identity that owns that
  relationship (see [architecture/03-service-boundaries.md](architecture/03-service-boundaries.md)).
- Every call to a dependency that has its own failure mode independent of
  the caller goes through `shared/circuit_breaker.py`, wrapped at the
  orchestrator/service layer, not inside the client itself (see
  `services/payment/app/services/payment_orchestrator.py`) — clients stay
  unaware of retry/breaker concerns. As of Phase 5 this means Payment's
  three outbound dependencies (fraud/user/provider); the Gateway's proxy
  calls are plain `httpx` with a timeout, not breaker-wrapped — proxying
  is pass-through by design (see
  [architecture/04-lld.md](architecture/04-lld.md)), and a breaker
  tripping there would need its own fallback story the Gateway doesn't
  have. Every retry/timeout value in a chain must respect
  [ADR-0012](decisions/ADR-0012-timeout-budgets-shrink-toward-the-leaves.md)
  — a caller's timeout needs real headroom over a callee's own
  worst-case retry duration, not just its single-attempt timeout.

## Agent Code Rules

- Agents never write directly to another service's database or call a
  payment provider — they call HTTP endpoints owned by the relevant
  service, or (for infrastructure actions) the Ops Controller only.
- Every LLM call produces a structured decision object (Pydantic model),
  never free text acted on directly. A non-LLM validator checks the
  decision against the action allowlist before anything executes — see
  [ADR-0005](decisions/ADR-0005-llm-actions-via-allowlisted-executor.md).
- Every agent action — accepted, rejected, or executed — is logged with
  the triggering telemetry, the decision, and the outcome, in the shared
  `agent_decisions` table.
- Agent loops are idempotent: re-observing the same state twice must not
  double-apply an action.
- Polling intervals and thresholds live in each agent's `config.py`, never
  hardcoded inline.

## Git Commit Conventions

[Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <short summary>
```

- **Types:** `feat`, `fix`, `refactor`, `chore`, `docs`, `test`, `perf`
- **Scope:** the service/agent name (`gateway`, `payment`, `fraud`, `user`,
  `notification`, `monitor`, `healer`, `ops-controller`, `shared`), or
  `repo` for cross-cutting changes.
- Imperative mood, lowercase, no trailing period.
- One logical change per commit; a roadmap phase may span several commits.
- Work happens on feature branches against a protected `main`, merged via
  pull request (Phase 12 wires up CI checks on the PR).

## Docker Standards

- Every service/agent has its own multi-stage `Dockerfile`
  (`python:3.12-slim` base): a `builder` stage installs deps, the final
  stage copies only the app + venv.
- No `latest` tags in `docker-compose.yml` — pin Redis, PostgreSQL, and
  Jaeger to explicit versions (no separate OTel Collector — see
  [ADR-0011](decisions/ADR-0011-otlp-direct-to-jaeger.md)).
- Every container runs as a **non-root user** and defines a `HEALTHCHECK`
  (or Compose `healthcheck:` block).
- The Ops Controller container is the *only* container granted access to
  the Docker socket/compose control needed for `restart-service` — no
  other service or agent container has that mount.
- Secrets are injected via environment variables from `.env`, never baked
  into an image layer.

## Observability Standards

- Every service calls `configure_tracing(service_name)` and
  `instrument_fastapi(app)` at startup (`shared/telemetry/setup.py`) —
  no exceptions, every service is a FastAPI app and every request should
  produce a trace.
- Beyond that, only instrument what a service actually uses — a shared
  module importing an instrumentation package a given service's
  `pyproject.toml` doesn't declare crashes that service at startup (this
  happened once: Fraud Service doesn't use httpx, so
  `shared/telemetry/setup.py`'s per-instrumentor imports are lazy,
  local to each `instrument_*` function, not top-level).

  | Service | `instrument_httpx()` | `instrument_sqlalchemy()` | `instrument_redis()` |
  |---|---|---|---|
  | gateway | yes — proxies every request | no DB | yes — rate limiter |
  | payment | yes — calls fraud/user/provider | yes | yes — event bus + idempotency lock |
  | fraud | no outbound calls | yes | no |
  | user | no outbound calls | yes | no |
  | notification | no outbound calls | yes | yes — event bus |
  | monitor (agent) | no — deliberate | no | no — deliberate |
  | healer (agent) | no — deliberate | no | no |
  | ops-controller | yes — calls Payment's breaker endpoints | no | no |
  | fraud-agent (agent) | no — calls User Service, but a freeze is rare/significant enough to find via structured logs instead | no | no — deliberate |

  The Monitor calls httpx (Jaeger) and Redis constantly but is not
  instrumented for either: tracing its own polling would feed the
  Monitor's activity back into the telemetry it analyzes. The Healer
  polls Jaeger during verification for the same reason. The Ops Controller
  is instrumented so a remediation's call into Payment is visible in
  Jaeger. The Fraud Agent's Redis calls (velocity window, freeze cooldown)
  are as constant and self-referential as the Monitor's polling, so they're
  exempt for the same reason.

- **Control-plane tables live in one place.** `shared/control_plane`
  defines `anomalies`, `incidents`, `agent_decisions` and `ops_actions` on
  one metadata object; every control-plane component creates the schema via
  `init_control_plane_schema` (advisory-locked — components start together).
  A new agent adds its tables there rather than defining a second `Base`.

- **Anything that can change system state goes through the Ops
  Controller.** New remediation capabilities are added to
  `agents/ops_controller/allowlist.py` in a reviewed commit and covered by
  an adversarial test in `tests/unit/test_ops_allowlist.py`; the
  allowlist test asserts the exact set of actions so an accidental addition
  fails CI.

- **Report failures on the span, not only in the response.** The Monitor
  computes error rate from spans. A failure that is returned as a normal
  HTTP response (e.g. a payment recorded as `failed` because a dependency
  is down, returned as 201) must additionally mark its span ERROR, or it
  is invisible to detection. Business rejections are not errors. See
  [ADR-0014](decisions/ADR-0014-monitor-derives-metrics-from-jaeger-traces.md).

- Redis pub/sub is the one hop OTel's auto-instrumentation can't see
  across (a published event is a string, not an HTTP request). A
  publisher that wants a consumer's processing linked into the same
  trace must call `shared.telemetry.inject_context(payload)` before
  publishing; a consumer that wants to continue that trace must call
  `shared.telemetry.extract_context(payload)` and pass the result as
  `context=` to `tracer.start_as_current_span(...)`. See
  `services/payment/app/services/payment_orchestrator.py` (publish side)
  and `services/notification/app/consumers/event_consumer.py` (consume
  side) for the reference implementation.
