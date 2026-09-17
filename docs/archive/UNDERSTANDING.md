# Understanding FlowGuard

This doc explains the core concepts behind FlowGuard in plain English —
written for whoever is building this while learning the concepts for the
first time. Come back to it whenever a term in another doc feels unfamiliar.

## What Are Microservices?

Instead of building one big application that does everything (accepts
payments, checks fraud, manages users, sends notifications), you split it
into several small, independent programs — each one responsible for a single
job. In FlowGuard, `payment`, `fraud`, `user`, and `notification` are each
their own tiny application, with their own code and their own process. They
talk to each other over the network (HTTP calls, or events through Redis)
instead of calling each other's functions directly in-process.

Why bother? Each piece can be built, tested, deployed, and — importantly for
this project — **fail** independently. If the notification service crashes,
payments can still go through; the system degrades instead of collapsing
entirely. The tradeoff is complexity: now you have network calls, partial
failures, and coordination problems that a single monolithic app wouldn't
have — which is exactly the complexity FlowGuard's resilience patterns
(circuit breakers, agents) exist to manage.

## What Is a Circuit Breaker?

Picture the payment service calling PayPal, and PayPal starts timing out.
Without protection, every incoming transaction waits out that timeout, one
by one, while doing nothing productive — the failure spreads and slows down
the whole system.

A circuit breaker sits in front of that risky call and watches for
failures. Once too many happen in a row, the breaker "opens": for a while,
it stops even trying to call PayPal and fails instantly instead, which is
fast and lets the rest of the system react (e.g. by switching to Razorpay).
After a cooldown, the breaker lets a single test call through ("half-open")
— if that succeeds, it closes again and normal traffic resumes; if it
fails, it stays open and waits longer. It's the same idea as an electrical
circuit breaker: better to cut power briefly than let a fault burn the whole
house down.

## What Is an Agent?

In FlowGuard, an "agent" is a small, always-running program that watches
what's happening in the system (through traces, logs, and health checks) and
decides what to do about problems — without a human clicking anything.

Concretely: the Supervisor agent notices a service has stopped responding to
health checks and decides to restart it. The Router agent notices PayPal is
suddenly slow and shifts new transactions to Razorpay. What makes these
"agents" rather than plain scripts is that some of their decisions — like
"is this a real outage or just noise?" — are ambiguous enough that we hand
them to an LLM (via the `anthropic` SDK) to judge, instead of hardcoding
every threshold by hand. The LLM's answer is still constrained to a strict,
structured decision that a plain, boring, predictable piece of code then
double-checks and executes — the model never gets direct access to touch
money or infrastructure itself.

## What Is OpenTelemetry?

When a single payment request travels through four different services, and
something goes wrong, you need to answer: which service was slow, which one
errored, and in what order did the calls happen? OpenTelemetry ("OTel") is a
standard way for every service to record that journey as a **trace** — a
tree of timed **spans**, one per operation ("call fraud service", "query
database", "call PayPal"), all tagged with a shared trace ID so they can be
stitched back together.

In FlowGuard, every service is instrumented with OTel, and all the spans get
shipped to a collector and then to Jaeger, a UI for browsing traces. This is
also the raw material the agent layer runs on — an agent doesn't watch logs
line by line, it watches structured trace and metric data to notice things
like "the fraud service's p99 latency just tripled."

## What Is Redis Pub/Sub?

Pub/sub ("publish/subscribe") is a messaging pattern: one part of the system
**publishes** a message to a named channel (like `transaction.completed`),
and any number of other parts can **subscribe** to that channel and get
notified the moment a message arrives — without the publisher knowing or
caring who's listening.

FlowGuard uses Redis's built-in pub/sub for this. When the payment service
finishes a transaction, it publishes an event; the notification service is
subscribed and reacts by sending a receipt. If you add a new service later
that also cares about completed transactions, it just subscribes too — the
payment service doesn't change at all. This is what decouples services from
each other: they don't need direct knowledge of who consumes their events.

## How Do All Pieces Connect?

Here's the full path, in plain terms:

1. A client asks the **payment service** to process a transaction.
2. Payment calls **fraud** to check risk, and **user** to check/update
   balance — both regular HTTP calls, each wrapped in a **circuit breaker**
   so a slow dependency can't stall the whole request forever.
3. Payment calls the actual **provider** (PayPal or Razorpay), also through
   a circuit breaker.
4. Once done, payment **publishes an event** to Redis; **notification**
   picks it up and tells the user, completely decoupled from the main
   request.
5. The whole time, every service is quietly emitting **OpenTelemetry**
   spans describing what it just did and how long it took.
6. Separately, the **agents** are continuously watching that telemetry
   stream. When something looks wrong — a breaker trips, error rates spike,
   a health check fails — an agent (sometimes with an LLM's help in judging
   ambiguous situations) decides on a fix and publishes a **control event**
   (like "restart the fraud service" or "route new traffic to Razorpay"),
   which the affected part of the system picks up and acts on.

So there are really two loops running at once: the **request loop**
(client → payment → fraud/user → provider → notification) that handles
actual money, and the **observation loop** (services → OTel → agents →
control events → services) that watches over the first loop and keeps it
healthy — without ever sitting directly in the path of a transaction.
