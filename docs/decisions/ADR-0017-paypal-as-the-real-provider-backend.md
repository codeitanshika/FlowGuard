# ADR-0017: PayPal as the Real Provider Backend, Selected Server-Side, Scoped to Its Redirect-less Reality

## Status
Accepted

## Context
Phase 10 (FR13) requires integrating one real payment provider sandbox —
authentication, order creation, capture, status lookup, and webhook
handling — behind the swappable `PaymentProvider` interface Phase 1
already defined (`MockPaymentProvider`). Three decisions were needed that
the Phase 0 design left open, plus one real architectural mismatch
discovered while building it.

## Decision

**1. PayPal, chosen by the user; verified against live docs, not
memory.** `.env.example` already scaffolded `PAYPAL_CLIENT_ID`/
`PAYPAL_CLIENT_SECRET` from the very first commit. Before writing any
integration code, the OAuth2 token endpoint, the Orders v2 create/capture
request and response shapes, and the webhook-verification endpoint were
each confirmed against PayPal's live documentation and OpenAPI schema —
this is exactly the kind of versioned external API where training data
can be stale, so nothing here was guessed.

**2. Provider selection is server-side config, not client input.**
`PAYMENT_PROVIDER_BACKEND=mock|paypal` (default `mock`, so no existing
demo or test needs new secrets); credentials are required and validated
at startup only when `paypal` is selected — the same fail-fast pattern
as `GATEWAY_JWT_SECRET`. This is a deliberate reading of `docs/api/api-
contracts.md`'s older note ("`provider` returns in the request once
Phase 10 makes provider selection real"): a merchant backend chooses its
own payment rail as a deployment decision, not per API call, the same
reasoning [ADR-0010](ADR-0010-static-client-credentials.md) already
applied to `GATEWAY_CLIENTS`. `PaymentRequest` stays exactly as Phase 1
left it.

**3. A capture creates an order and immediately attempts to capture it —
and PayPal's real, documented answer to that is a decline, treated as
one.** FlowGuard's Payment Service has no buyer-redirect step anywhere in
its API (`PaymentRequest` is `{user_id, amount, currency}`, nothing
resembling an approval callback). PayPal's Orders API is fundamentally a
checkout flow: capturing an order the buyer never approved returns a
real, stable, documented error — `422 UNPROCESSABLE_ENTITY`, issue
`ORDER_NOT_APPROVED`. Rather than build a buyer-redirect flow that
doesn't fit anywhere else in this system's architecture (and that FR13
doesn't ask for), `PayPalProvider.capture()` does the honest thing: make
the real calls, and interpret PayPal's real answer correctly.
`ORDER_NOT_APPROVED` (and a short list of other well-known decline
issues — `INSTRUMENT_DECLINED`, `PAYER_ACTION_REQUIRED`,
`TRANSACTION_REFUSED`, `DUPLICATE_INVOICE_ID`, `AMOUNT_MISMATCH`) is
classified as a **provider decline** (`ProviderResult.success=False`),
the same tier as `MockPaymentProvider`'s amount-0.13 decline — PayPal
understood the request and correctly said no, which is not an outage.
Every other failure (auth failure, network error, 5xx, an unrecognized
4xx) is a **dependency failure** and counts toward the provider circuit
breaker exactly like the mock provider. Verified live against PayPal's
real sandbox with intentionally invalid credentials: a genuine HTTPS
call reached `api-m.sandbox.paypal.com`, got back a real `401
invalid_client`, correctly tripped the breaker's retry, and the
orchestrator's existing compensating-debit path fired — none of that
needed to change for a real provider to slot in.

**4. Webhook verification calls PayPal's own verify-signature endpoint,
not local certificate/crypto handling.** `POST /v1/notifications/verify-
webhook-signature` exists specifically so integrators don't reimplement
PayPal's signature scheme; `PayPalWebhookVerifier` sends the five
`PAYPAL-*` headers plus the stored `webhook_id` and the raw event body,
and trusts `verification_status`. This is optional and independently
gated on `PAYPAL_WEBHOOK_ID` being configured — capture works without it.

**5. `app` is a package name every service reuses, so PayPal's tests run
as their own pytest invocation.** `services/payment/tests/` (its own
`conftest.py`, its own `pythonpath` entry in `services/payment/
pyproject.toml`) rather than `tests/unit/` at the repo root, which can
only ever import one service's `app` package at a time. Run with `cd
services/payment && python -m pytest`.

## Consequences
- **Known gap — never tested against real PayPal credentials.** No
  sandbox account was available while building this (the user explicitly
  deferred that). 28 unit tests exercise the verified request/response
  shapes over a mock HTTP transport; live verification only reached
  PayPal's real auth endpoint with intentionally wrong credentials
  (proving connectivity and correct error handling, not a successful
  capture). The first real capture attempt with valid sandbox credentials
  should be watched — same posture as the Healer's and Fraud Agent's
  untested LLM paths in Phases 8-9.
- **Accepted cost — a real PayPal capture always declines today.**
  Without a buyer-approval redirect anywhere in this system, every
  `payment_source`-less order created this way will hit
  `ORDER_NOT_APPROVED` on capture. This is intentional scope, not a bug:
  FR13 asks for the five integration capabilities to exist and be
  correct, not for a full checkout UX this project's architecture was
  never designed to have. A real "money moves" demo needs either a
  redirect flow (a real scope expansion) or a vaulted/direct payment
  source (needs its own sandbox setup); both are future work.
- **Benefit:** the provider circuit breaker, fault injection, the
  Monitor/Healer/Ops Controller loop, and the idempotent-replay contract
  all needed zero changes to work with a second, real provider —
  concrete proof the Phase 1 `PaymentProvider` abstraction actually holds.
- **Webhook handling is feature-complete but unverified against a real
  delivery** — no registered sandbox webhook exists to send one.

## Alternatives Considered
- **Build the full PayPal wallet redirect flow (approve URL, Gateway
  callback route, order polling):** rejected as out of scope — it would
  add a genuinely new request/response shape to the Gateway/Payment API
  that nothing else in this system has, for a capability FR13 doesn't
  require.
- **PayPal's direct/vaulted card payment_source (server-to-server
  capture with no redirect at all):** the real way to get an actual
  successful capture without a redirect, but it requires its own sandbox
  setup (a vaulted payment method or raw card submission) this session
  had no credentials to verify against; revisit once real sandbox access
  exists.
- **Silently fall back to MockPaymentProvider if PayPal credentials are
  missing:** rejected — same reasoning as `GATEWAY_JWT_SECRET`: a
  deployment that asked for `paypal` and got `mock` instead is a config
  bug that should fail loudly, not a degraded mode to hide.
- **Local PayPal webhook signature verification (fetch their cert,
  verify manually):** rejected in favor of their hosted verification
  endpoint — no reason to reimplement crypto PayPal already validates
  for free.
