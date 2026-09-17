# FlowGuard — High-Level Design

## Component Diagram

```mermaid
flowchart TB
    Client["Merchant / Test Client"]

    subgraph DataPlane["Data Plane — request path"]
        GW["API Gateway<br/>authn/authz, rate limit, routing"]
        US["User Service"]
        PS["Payment Service"]
        FS["Fraud Service<br/>(sync risk check)"]
        NS["Notification Service"]
        PROV["Payment Provider Sandbox"]
    end

    subgraph Infra["Shared Infrastructure"]
        REDIS[(Redis<br/>event bus / state / rate limits)]
        PG[(PostgreSQL<br/>per-service schemas)]
        OTEL["OpenTelemetry Collector"]
        JAEGER["Jaeger UI"]
    end

    subgraph ControlPlane["Control Plane — self-healing loop"]
        MON["Monitor Agent"]
        HEAL["Healer Agent"]
        FRAUDAGENT["Fraud Agent<br/>(async deep analysis)"]
        OPS["Ops Controller<br/>(allowlisted action executor)"]
    end

    Client --> GW
    GW --> US
    GW --> PS
    PS --> FS
    PS --> US
    PS --> PROV
    PS -. payment.created .-> REDIS
    PS -. payment.completed/.failed .-> REDIS
    REDIS -. notify .-> NS
    REDIS -. consume .-> FRAUDAGENT
    FRAUDAGENT -->|freeze call| US
    FRAUDAGENT -. fraud.user_frozen .-> REDIS

    US --> PG
    PS --> PG
    FS --> PG
    NS --> PG

    GW -.traces.-> OTEL
    US -.traces.-> OTEL
    PS -.traces.-> OTEL
    FS -.traces.-> OTEL
    NS -.traces.-> OTEL
    OTEL --> JAEGER
    OTEL -.metrics.-> MON

    MON -. anomaly.detected .-> REDIS
    REDIS -. consume .-> HEAL
    HEAL -->|LLM call| LLM["Anthropic API"]
    HEAL -->|allowlisted action| OPS
    OPS -->|internal endpoint call| PS
    OPS -->|internal endpoint call| GW
    OPS -->|controlled restart| Infra
```

## Two Loops, Not One System

FlowGuard is best understood as **two loops sharing infrastructure**:

1. **The request loop (data plane)** — `Client → Gateway → Payment → Fraud/User → Provider → Notification`. This is what processes money. It must be fast, predictable, and boring. Nothing AI-related is allowed to sit inside it synchronously.
2. **The observation loop (control plane)** — `Services → OpenTelemetry → Monitor Agent → Healer Agent → Ops Controller → Services`. This watches the first loop and repairs it. It runs continuously, independent of any single request, and is where all AI reasoning lives.

The two loops only touch at narrow, well-defined points: telemetry flowing out of the data plane, and allowlisted action calls flowing back in through the Ops Controller. This separation is what makes it safe to let an LLM participate at all — see [ADR-0005](../decisions/ADR-0005-llm-actions-via-allowlisted-executor.md).

## Why Every Component Exists

| Component | Why it exists |
|---|---|
| **API Gateway** | Single entry point so authn/authz, rate limiting, and request/trace-ID injection are implemented once, not reinvented per service. Without it, every service independently owns security-critical code — inconsistent and harder to audit. |
| **User Service** | The one authoritative source for "does this user exist / what's their balance / are they frozen." No other service duplicates or guesses this state. |
| **Payment Service** | Owns the transaction lifecycle and idempotency. The "process a payment" business process lives in exactly one place; it orchestrates User, Fraud, and the provider — it doesn't contain their logic. |
| **Fraud Service** | A fast, synchronous, mostly-deterministic risk check that can block an obviously bad transaction before money moves. Kept separate from Payment so risk rules evolve independently and a slow fraud check can be circuit-broken without touching payment code. |
| **Notification Service** | Decouples "tell the user" from the critical path — a slow notification provider must never delay or fail a payment. |
| **Redis** | One low-latency dependency serving several ephemeral needs: pub/sub event bus, circuit breaker state, rate-limit counters, fraud velocity sliding windows. See [ADR-0002](../decisions/ADR-0002-redis-for-event-bus-and-state.md). |
| **PostgreSQL** | Durable system of record for anything that must survive a restart and be queried/audited later: transactions, users, risk assessments, incidents, agent decisions. |
| **OpenTelemetry + Jaeger** | A uniform, queryable signal of what the system is doing across every service boundary — the raw material both a human operator and the Monitor Agent reason over. See [ADR-0004](../decisions/ADR-0004-opentelemetry-for-observability.md). |
| **Monitor Agent** | Continuously turns telemetry into a judgment ("is this normal or anomalous") so nobody has to watch a dashboard. First link in the self-healing loop. |
| **Healer Agent** | Turns a detected anomaly into a bounded, auditable corrective action. Deliberately separate from Monitor so detection and remediation can be reasoned about, tested, and evolved independently. |
| **Fraud Agent** | Turns the Fraud Service's raw signal plus behavioral context (velocity, geo) into a freeze decision and narrative. Kept separate from Fraud Service's synchronous scoring so slower, LLM-backed reasoning never blocks a payment request. |
| **Ops Controller** | The single, narrow, allowlisted surface that turns a Healer decision into a real infrastructure action. Exists specifically so no AI-produced output ever reaches a shell or Docker socket directly. See [ADR-0005](../decisions/ADR-0005-llm-actions-via-allowlisted-executor.md). |
| **Payment Provider Sandbox** | The external system being integrated against, abstracted behind an interface so the specific provider is a swappable implementation detail (see Phase 10), not baked into Payment Service's core logic. |

## Request Path (Happy Path)

```
Client
  → API Gateway (authn, rate limit, trace-id)
    → Payment Service
        → Fraud Service            [sync risk check, circuit-broken]
        → User Service             [balance check/debit, circuit-broken]
        → Payment Provider Sandbox [capture, circuit-broken]
      → publish payment.created / payment.completed (Redis)
  ← response to Client

(async, off the request path)
Redis payment.completed → Notification Service → user notified
Redis payment.created   → Fraud Agent → velocity/geo analysis → (maybe) freeze via User Service
```

## Control Path (Self-Healing Loop)

```
All services → OTel spans/metrics → OTel Collector → Jaeger (human view)
                                                      → Monitor Agent (machine view)

Monitor Agent: compute error rate / p95 / p99 / throughput per service
  → threshold breach → publish anomaly.detected (Redis) + persist to PostgreSQL

Healer Agent (subscribed to anomaly.detected):
  → pull trace context for the anomaly
  → inspect current service/circuit-breaker state
  → call LLM with structured context → structured diagnosis + proposed action
  → validate against action allowlist (schema + allowlist, see ADR-0005)
  → call Ops Controller with the validated action
  → verify recovery (re-check health/metrics)
  → write incident report to PostgreSQL, publish incident.resolved
```

## Deployment View (Local)

All components run as containers under Docker Compose (Phase 3). Services and agents are separate containers; Redis, PostgreSQL, the OTel Collector, and Jaeger are shared infrastructure containers. The Ops Controller is the only container with a mount/permission allowing it to call `docker compose restart <service>` — no other container has that capability, including the agents.
