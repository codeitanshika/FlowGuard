# Understanding FlowGuard

Supersedes the old `UNDERSTANDING.md` (now in
[archive/](archive/UNDERSTANDING.md)) — same plain-English teaching intent,
updated with the Gateway and the allowlisted-action safety boundary, and
corrected agent names (Monitor, Healer, Fraud Agent — not Supervisor/
Router/Fraud Analyst from the earlier draft).

## What Are Microservices?

Instead of one big application doing everything, FlowGuard splits the work
into small, independent programs — `gateway`, `payment`, `fraud`, `user`,
`notification` — each responsible for one job, each its own process,
talking over the network instead of calling each other's functions
in-process. This means each piece can fail independently: if Notification
crashes, payments still go through. The tradeoff is real network calls,
partial failures, and coordination problems a single app wouldn't have —
which is exactly the complexity circuit breakers and the agent layer exist
to manage.

## What Is an API Gateway?

Before any request reaches a service, it passes through one single door:
the Gateway. It checks who you are (authentication), whether you're
allowed to do what you're asking (authorization), and how fast you're
allowed to ask (rate limiting) — once, in one place, instead of every
service reimplementing that logic separately and inconsistently. It's also
where a trace ID gets stamped onto the request so the whole journey can be
followed later in Jaeger.

## What Is a Circuit Breaker?

Picture Payment Service calling the payment provider, and the provider
starts timing out. Without protection, every incoming transaction waits
out that timeout one by one — the failure spreads and slows the whole
system down. A circuit breaker watches for failures; once too many happen
in a row, it "opens": for a while it stops even trying and fails instantly
instead, which is fast and lets the rest of the system react. After a
cooldown it lets one test call through ("half-open") — success closes it
again, failure keeps it open longer. Same idea as an electrical breaker:
better to cut power briefly than let a fault spread.

## What Is an Agent?

An "agent" here is a small, always-running program that watches telemetry
and decides what to do about problems, without a human clicking anything.
The **Monitor Agent** notices error rates or latency crossing a threshold
and raises an anomaly. The **Healer Agent** takes that anomaly, reasons
about the likely cause — sometimes with an LLM's help for ambiguous
judgment calls — and picks a fix. The **Fraud Agent** watches transaction
patterns for a given user and decides whether the account looks risky
enough to freeze. None of them sit inside a live payment request; they
watch and react from the side.

## What Is the Ops Controller, and Why Can't the LLM Just Run Commands?

This is the one piece of FlowGuard that exists purely for safety. The
Healer Agent's LLM call can produce a *proposal* — "I think the payment
provider is down, open its circuit breaker" — but that text never touches
infrastructure directly. It gets checked against a strict schema, then
checked against a fixed, hardcoded list of allowed actions (open a
breaker, reset a breaker, shed traffic, restart a specific service). Only
if it matches something on that list does the Ops Controller — a separate,
narrow component — actually carry it out. If the model hallucinates or
proposes something outside that list, nothing happens except a rejected,
logged attempt. The LLM never gets a shell or a Docker socket; it only
ever produces data that a fixed piece of ordinary code decides whether to
act on. See [decisions/ADR-0005](decisions/ADR-0005-llm-actions-via-allowlisted-executor.md).

## What Is OpenTelemetry?

When a payment request travels through Gateway → Payment → Fraud →
Notification, and something goes wrong, you need to know which service was
slow or which one errored, and in what order. OpenTelemetry is a standard
way for every service to record that journey as a **trace** — a tree of
timed **spans**, all tagged with a shared trace ID. FlowGuard ships every
span to Jaeger for humans to browse, and the Monitor Agent watches the same
structured data to notice things like "Fraud Service's p99 latency just
tripled."

## What Is Redis Pub/Sub?

One part of the system **publishes** a message to a named channel (like
`payment.completed`), and anything **subscribed** to that channel gets
notified — without the publisher knowing or caring who's listening.
FlowGuard uses Redis for this: Payment Service publishes an event, and
Notification Service (and separately, the Fraud Agent) react to it. This
is what decouples services from each other — a new subscriber can be added
later without Payment Service changing at all.

## How Do All Pieces Connect?

1. A client authenticates through the **Gateway**, which checks the
   request and forwards it to **Payment Service**.
2. Payment calls **Fraud Service** (a fast, synchronous risk check) and
   **User Service** (balance), each through a **circuit breaker**.
3. Payment calls the **provider sandbox**, also breaker-wrapped.
4. Payment **publishes an event**; **Notification Service** and the
   **Fraud Agent** react independently, off the critical path.
5. The whole time, every service emits **OpenTelemetry** spans.
6. The **Monitor Agent** watches that telemetry continuously; on an
   anomaly it publishes `anomaly.detected`.
7. The **Healer Agent** picks that up, reasons about it (with LLM help for
   the ambiguous parts), and — only through the **Ops Controller**'s
   fixed allowlist — takes a corrective action, then verifies recovery and
   logs the whole incident.

Two loops running at once: the **request loop** that handles actual money,
and the **observation loop** that watches over it and keeps it healthy —
without ever sitting directly inside a live transaction.
