# FlowGuard — Roadmap

Supersedes the old `BUILD_PLAN.md` (now in [archive/](archive/BUILD_PLAN.md)),
which was written before the Gateway, the Monitor/Healer/Fraud Agent split,
and the Ops Controller existed as named components. This roadmap tracks the
actual 15-phase plan the project is being built against.

Each phase is built, demoed, and committed before the next one starts —
see the working style in [architecture/README.md](architecture/README.md).

| Phase | Goal | Status |
|---|---|---|
| 0 | System design: requirements, HLD/LLD, service boundaries, schemas, event flows, failure scenarios, ADRs | ✅ Done — see [architecture/](architecture/) and [decisions/](decisions/) |
| 1 | Core microservices: Gateway, Payment, User, Fraud, Notification — real API contracts, persistence, health endpoints | ✅ Done |
| 2 | API Gateway security: JWT auth, authorization, rate limiting, idempotency keys, request/trace IDs | ✅ Done |
| 3 | Docker + local infra: Dockerfiles, `docker-compose.yml`, Postgres, Redis, Jaeger, health checks, non-root containers | ✅ Done |
| 4 | Observability: OpenTelemetry across all services, traces exported to Jaeger | ✅ Done |
| 5 | Resilience: circuit breaker (CLOSED→OPEN→HALF_OPEN→CLOSED) in Redis, timeouts, retries, backoff, idempotency | ✅ Done |
| 6 | Fault injection: controlled HTTP 500s, latency, timeouts, provider failures, configurable error rate | Planned |
| 7 | Monitor Agent: telemetry → error rate / p95 / p99 / throughput → anomaly detection → `anomaly.detected` | Planned |
| 8 | Healer Agent: anomaly → diagnosis (LLM) → schema-validated decision → allowlisted action via Ops Controller → verify → `incident.resolved` | Planned |
| 9 | Fraud Agent: velocity/geo analysis → deterministic risk decision → LLM narrative → freeze via User Service → notification | Planned |
| 10 | Real payment sandbox integration (one provider first), behind a swappable abstraction | Planned |
| 11 | Testing + chaos testing: unit, integration, API, failure, agent tests; MTTR/recovery-rate measurement | Planned |
| 12 | CI/CD: GitHub Actions — lint, format, tests, security checks, Docker build, staging/production pipeline | Planned |
| 13 | Cloud deployment: simple architecture (no Kubernetes), networking, secrets, HTTPS, rollback strategy | Planned |
| 14 | SRE + production monitoring: availability, error rate, latency, MTTR, agent decisions, LLM cost/latency dashboards and alerts | Planned |
| 15 | AI evaluation + safety: incident evaluation dataset, detection/root-cause/action accuracy, false positive/negative rates, prompt/version tracking | Planned |

## Current Component Set (as of Phase 0)

- **Services:** `gateway`, `user`, `payment`, `fraud`, `notification`
- **Agents:** `monitor`, `healer`, `fraud` (agent — distinct from the Fraud *Service*)
- **Control-plane support:** Ops Controller (allowlisted action executor, see [ADR-0005](decisions/ADR-0005-llm-actions-via-allowlisted-executor.md))
- **Infra:** Redis, PostgreSQL (database-per-service), OpenTelemetry Collector, Jaeger

No dashboard/UI service is currently planned — Phase 14's "dashboards" refers
to operational monitoring dashboards (e.g. Grafana or Jaeger itself), not a
custom-built frontend. If a custom operator UI is added later, it will get
its own phase and its own design doc rather than being retrofitted here.
