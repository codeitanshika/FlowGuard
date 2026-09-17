# FlowGuard — Project Overview

## Problem Statement

Payment systems fail in ways that are expensive and hard to catch quickly:
a provider degrades silently, a downstream fraud check times out, a
dependency loop causes cascading errors — and by the time a human notices
the dashboard or the pager, real transactions have already been lost or
mishandled. Most portfolio-scale payment projects stop at "process a
payment"; they don't address what happens when a piece of the system
degrades in production.

## Solution

FlowGuard is a payment platform built from independent microservices
(payment, fraud, user, notification) with an added **agent layer** that
watches the system's own telemetry (traces, health checks, error rates) and
takes corrective action automatically — tripping circuit breakers, rerouting
traffic between payment providers, restarting unhealthy services, and
tightening fraud thresholds during anomalous activity. The system is
designed to degrade gracefully and recover on its own, with every decision
logged and visible on an operator dashboard.

## Key Features

- Multi-provider payments (PayPal + Razorpay sandbox) with automatic
  failover between them.
- Real-time fraud scoring on every transaction.
- Circuit breakers around every external/inter-service dependency.
- Autonomous agents (Supervisor, Healer, Fraud Analyst, Router) that observe
  OpenTelemetry data and act without human input.
- Full distributed tracing (Jaeger) across services and agent decisions.
- A fault-injection mode to demonstrate self-healing behavior on demand.
- An operator dashboard showing live service health, breaker states, and an
  audit trail of every agent action.

## Target Companies and How to Switch

FlowGuard's core skills map onto payments, infra/reliability, and platform
engineering roles. The same codebase supports several framings — swap the
emphasis, not the code:

| Target | What to emphasize |
|---|---|
| Payments companies (Stripe, PayPal, Razorpay, Adyen) | Provider integration, idempotency, transaction correctness, fraud scoring |
| Infra/reliability-focused companies (Datadog, HashiCorp, cloud providers) | Circuit breakers, OpenTelemetry instrumentation, self-healing agent behavior |
| AI/agent platform companies (Anthropic, agent-tooling startups) | The agent layer itself — structured LLM decisions, safe action execution, multi-agent coordination |
| General backend/platform roles | The full-stack story: microservices, observability, resilience, and automation working together |

To switch framing for a given application, adjust the README/demo script and
which parts of the system you walk through first — the underlying
architecture doesn't need to change.

## Score Breakdown (Why This Is a 9/10 Project)

| Criterion | Notes | Score |
|---|---|---|
| Real-world relevance | Payments + reliability is a domain every backend team cares about | 9/10 |
| Technical depth | Distributed tracing, circuit breakers, multi-service async architecture | 9/10 |
| Novelty | Self-healing via LLM-driven agents (not just fixed-threshold automation) is uncommon in portfolio projects | 10/10 |
| Demonstrability | Fault injection makes the self-healing behavior visible in a live demo, not just claimed in a README | 9/10 |
| Completeness | Spans backend, infra, observability, and UI in one coherent system | 8/10 |
| **Overall** | | **9/10** |

Points held back mainly for scope: FlowGuard intentionally does not attempt
full PCI compliance, multi-region deployment, or a production-grade fraud
model — it demonstrates the patterns at portfolio scale, not production
scale.

## Project Stats

- **Services:** 4 (`payment`, `fraud`, `user`, `notification`)
- **Agents:** 4 (`Supervisor`, `Healer`, `Fraud Analyst`, `Router`)
- **Payment providers integrated:** 2 (PayPal, Razorpay — sandbox)
- **Build modules:** 12 (see `BUILD_PLAN.md`)
- **Core infra dependencies:** Redis, PostgreSQL, OpenTelemetry Collector, Jaeger
- **Primary language:** Python 3.12 (FastAPI, asyncpg, httpx)
