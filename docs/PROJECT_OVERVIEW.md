# FlowGuard — Project Overview

Supersedes the old `PROJECT_OVERVIEW.md` (now in
[archive/](archive/PROJECT_OVERVIEW.md)) — updated for the current
component set (Gateway added, agents renamed/reduced to Monitor/Healer/
Fraud Agent, Ops Controller introduced as the AI-safety boundary).

## Problem Statement

Payment systems fail in ways that are expensive and hard to catch quickly:
a provider degrades silently, a downstream fraud check times out, a
dependency loop causes cascading errors — and by the time a human notices,
real transactions have already been lost or mishandled. Most portfolio-
scale payment projects stop at "process a payment"; they don't address
what happens when a piece of the system degrades in production, and they
rarely take AI-safety seriously when adding automation.

## Solution

FlowGuard is a payment platform built from independent microservices
(Gateway, Payment, Fraud, User, Notification) with a separate agent layer
(Monitor, Healer, Fraud Agent) that watches the system's own telemetry and
takes corrective action automatically — tripping circuit breakers,
restarting unhealthy services, freezing accounts on high fraud risk — all
executed through a single narrow, allowlisted component (the Ops
Controller) so no AI-generated output ever reaches a shell or Docker
socket directly. Every decision is logged for audit.

## Key Features

- A single authenticated entry point (API Gateway) with rate limiting and
  idempotency-key support for all payment operations.
- Real-time synchronous fraud scoring on every transaction, plus
  asynchronous velocity/geo analysis for account-level risk.
- Circuit breakers around every inter-service and provider dependency.
- Autonomous agents (Monitor, Healer, Fraud Agent) that observe
  OpenTelemetry data and act without human input, through a
  safety-bounded action layer.
- Full distributed tracing (Jaeger) across every service and agent
  decision.
- A controlled fault-injection mode to demonstrate self-healing behavior
  on demand.
- A real payment provider sandbox integration (PayPal) behind a
  swappable abstraction — proven swappable, not just designed that way:
  Phase 10 added it alongside the original mock provider with zero
  changes to the orchestrator, the circuit breaker, or fault injection.

## Target Companies and How to Switch

The same codebase supports several framings — swap the emphasis, not the
code:

| Target | What to emphasize |
|---|---|
| Payments companies (Stripe, PayPal, Razorpay, Adyen) | Gateway idempotency, provider abstraction, transaction correctness, fraud scoring |
| Infra/reliability-focused companies (Datadog, HashiCorp, cloud providers) | Circuit breakers, OpenTelemetry instrumentation, the Monitor→Healer self-healing loop |
| AI/agent platform companies (Anthropic, agent-tooling startups) | The allowlisted-action safety boundary (Ops Controller), structured LLM decisions, deterministic-first fraud reasoning |
| General backend/platform roles | The full-stack story: Gateway security, microservices, observability, resilience, and automation working together |

## Score Breakdown (Why This Is a 9/10 Project)

| Criterion | Notes | Score |
|---|---|---|
| Real-world relevance | Payments + reliability is a domain every backend team cares about | 9/10 |
| Technical depth | Gateway security, distributed tracing, per-dependency circuit breakers, async agent architecture | 9/10 |
| Novelty | Self-healing via LLM-driven agents *with an explicit, architected safety boundary* — not just fixed-threshold automation — is uncommon in portfolio projects | 10/10 |
| Demonstrability | Fault injection makes the self-healing behavior visible in a live demo, not just claimed in a README | 9/10 |
| Completeness | Spans backend, infra, observability, security, and AI safety in one coherent system | 8/10 |
| **Overall** | | **9/10** |

Points held back mainly for scope: FlowGuard intentionally does not
attempt full PCI compliance, multi-region deployment, or Kubernetes — it
demonstrates the patterns at portfolio scale, not production scale.

## Project Stats

- **Services:** 5 (`gateway`, `payment`, `fraud`, `user`, `notification`)
- **Agents:** 3 (`monitor`, `healer`, `fraud`)
- **Safety-critical control component:** 1 (Ops Controller — allowlisted action executor)
- **Payment providers integrated:** 1 real sandbox — PayPal (Phase 10) — plus the original mock, both behind the same provider-swappable abstraction
- **Build phases:** 15 (see [ROADMAP.md](ROADMAP.md))
- **Core infra dependencies:** Redis, PostgreSQL, OpenTelemetry Collector, Jaeger
- **Primary language:** Python 3.12 (FastAPI, asyncpg, httpx)
