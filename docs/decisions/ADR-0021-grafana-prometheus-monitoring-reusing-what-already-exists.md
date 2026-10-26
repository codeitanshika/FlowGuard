# ADR-0021: Grafana + Prometheus for Monitoring, Reusing What Already Exists

## Status
Accepted

## Context
Phase 14 needs "SRE + production monitoring: availability, error rate,
latency, MTTR, agent decisions, LLM cost/latency dashboards and alerts."
`docs/ROADMAP.md`'s own framing for this phase already says "dashboards"
means operational dashboards (Grafana/Jaeger), not a custom frontend — no
new product surface, just visibility into what the system already does.

Three of the six things this phase asks for already exist as raw data
with no dashboard on top: the Monitor Agent computes error_rate/p95/p99/
throughput every poll cycle (`agents/monitor/detector.py`) but exposes it
only as logs; `incidents.opened_at`/`resolved_at` already give real MTTR
(the chaos tests already compute it this way — ADR-0018); and
`agent_decisions` already has `llm_tokens_in`/`llm_tokens_out` per
decision (NFR10's "token/cost usage is tracked per decision" — except
the *cost* half was never actually computed, only the token counts).

## Decision

**1. Monitor Agent exposes `/metrics` from the snapshot it already
computes, not a second telemetry path.** `agents/monitor/metrics_export.py`
holds a small set of Prometheus Gauges (not Counters — the values are
over a sliding window and can legitimately go down between scrapes),
updated once per poll cycle from the same `MetricsSnapshot` that already
feeds anomaly detection. No second Jaeger query, no duplicated
computation — the exposition is a second *reader* of one number, not a
second *source* of it.

**2. A real dollar figure for LLM cost, closing an actual NFR10 gap.**
`shared/llm_pricing.py` holds Anthropic's current per-model per-token
rates (verified live, not from training-data memory, same discipline as
every external price/version in this project) and a `cost_usd()`
function; `agent_decisions.llm_cost_usd` is computed from it at write
time in both `agents/healer/db.py` and `agents/fraud/db.py`'s
`record_decision`. Null whenever the tokens are null (the rules
fallback) or the model isn't in the price table — never guessed.

**3. Two datasources, one per kind of question — not Prometheus for
everything.** Prometheus is the right tool for continuously-sampled
gauges (availability, error rate, latency, throughput); MTTR, agent
decisions, and LLM cost are inherently record-shaped — "how long did
*this* incident take", "what did *this* decision cost" — and SQL fits
that better than forcing it through a metrics format first. Grafana's
Postgres datasource queries `flowguard_control` directly, the same
database the agents already write to; nothing new is piped anywhere.

**4. Grafana's own built-in alerting, not a separate Alertmanager.**
Consistent with this project's existing bias (ADR-0002: Redis over
Kafka; ADR-0011: no separate OTel Collector) — Grafana's unified
alerting can evaluate rules against both datasources directly, so adding
Alertmanager as a second routing layer on top would be a second
component doing a subset of what Grafana already does. Two rules are
provisioned: an error-rate threshold against Prometheus, and "an
incident has been open longer than the NFR11 120s budget" against
Postgres (count of `incidents` rows past that age, not yet resolved) —
one per datasource kind, matching decision 3.

**5. The contact point is a disposable local webhook receiver, not a
fabricated Slack/PagerDuty URL.** `infra/monitoring/alertsink/` is a
~20-line stdlib HTTP server that logs whatever it receives — its only
purpose is making "does the alert actually fire and get delivered"
verifiable without inventing credentials for a real external service. A
real deployment swaps the contact point's URL; this ADR's Consequences
section says so explicitly rather than leaving it implicit.

**6. All of this is a new, local-only compose overlay — not part of
`docker-compose.prod.yml`.** `infra/docker/docker-compose.monitoring.yml`
adds Prometheus, Grafana, and the alertsink; none of the three are
published anywhere beyond this overlay. Same posture as the Ops
Controller's loopback-only publication (Phase 8): these are operator
tools, not public surface, so they don't belong in the production
topology ADR-0020 already defined.

**7. Dashboards and alert rules are provisioned as files, not clicked
together.** `infra/monitoring/grafana/provisioning/` and
`infra/monitoring/grafana/dashboards/*.json` are version-controlled, so
the monitoring setup survives a `docker compose down -v` and a fresh
clone the same way every other part of this stack does. Three
dashboards: Service Health (Prometheus), Self-Healing (Postgres: MTTR,
incident outcomes, rules-vs-LLM decision source, recent decisions), LLM
Cost & Latency (Postgres: cost/latency over time by agent, recent calls).

**8. Verified against the real stack, not just read back.** The exact
discipline every prior phase used: `/metrics` was curled from inside the
Docker network and showed real per-service gauges; Prometheus's own
`/api/v1/targets` showed the scrape as `up`; Grafana's datasource health
check confirmed a live Postgres connection; a real chaos scenario
(`tests/chaos/test_provider_outage_recovery.py`) produced real rows in
`incidents` and `agent_decisions`, confirmed queryable through Grafana's
own `/api/ds/query` using the exact `rawSql` the dashboards use; and a
sustained real fault (60% error rate on the Fraud service, ~170s of real
traffic) made the error-rate alert rule actually transition to `firing`
and deliver a real webhook payload to the alertsink, confirmed from its
own logs — not inferred from the rule existing.

Two real bugs were found this way, not by inspection:
   - **The exact relative-path pitfall ADR-0020 already documented was
     made again and caught the same way.** `docker-compose.monitoring.yml`
     used `../monitoring/...` assuming resolution relative to
     `infra/docker/` (this file's own directory); Compose resolves
     relative bind-mount paths against the *project* directory (repo
     root, from `docker-compose.yml` being listed first) for every `-f`
     file in a multi-file invocation, not each file's own directory. The
     alertsink container's actual exit (`can't open file
     '/alertsink/server.py'`) is what caught it — fixed to
     `./infra/monitoring/...`.
   - **Prometheus's own alert-evaluation cadence vs. the self-healing
     loop's own speed.** The first attempt to verify the error-rate alert
     used the standard chaos-test fault duration (90s); it never fired,
     because the rule's `for: 2m` pending period needs the condition
     sustained longer than that fault lasted — the self-healing loop (a
     measured ~31s MTTR in the same run) had already resolved things
     before the alert's own window elapsed. Rather than shortening `for`
     to make a demo fire faster, this was left alone and re-verified with
     a longer, directly-injected fault (170s): a `for: 2m` alert existing
     specifically to catch what self-healing *didn't* fix within its own
     window is the correct design, not a bug — paging a human for
     something auto-remediated in 31s would be the actual mistake.

## Consequences
- **Known gap — the webhook contact point is a local verification tool,
  not a real notification channel.** Swapping
  `infra/monitoring/grafana/provisioning/alerting/contactpoints.yml`'s
  URL for a real Slack/PagerDuty/etc. webhook is a deployment-time
  choice, deliberately not made here (same reasoning as every other
  "build it correctly, don't fabricate credentials for it" decision in
  this project — Phase 10's PayPal sandbox, Phase 13's Terraform).
- **Known gap — Grafana's Postgres datasource uses the same local-dev
  superuser credential every other service in `docker-compose.yml`
  already does**, not a dedicated least-privilege read-only role. Fine
  for this overlay (local-only, operator-only, never in the production
  compose file); a real deployment pointing Grafana at a production
  Postgres should give it its own read-only role first.
- **Known gap — LLM pricing is a hand-maintained constant
  (`shared/llm_pricing.py`), not derived from any API response.** It
  drifts silently if Anthropic's published rate for a model changes;
  there is no staleness check. An unpriced or unrecognized model simply
  gets no cost figure rather than a wrong one, which is the safer failure
  mode, but it is still a manual-upkeep gap worth naming.
- **Benefit:** every number on every dashboard is something the system
  already computed for its own purposes (anomaly detection, incident
  tracking, decision auditing) — Phase 14 added visibility, not a new
  source of truth to keep in sync with the real one.

## Alternatives Considered
- **A full OTel Collector + a metrics pipeline from scratch:** rejected
  — reintroduces exactly the separate Collector ADR-0011 already decided
  against, to compute numbers the Monitor Agent already computes itself.
- **Prometheus Alertmanager as a second routing layer:** rejected —
  Grafana's own unified alerting already evaluates rules against both
  datasources and routes to contact points; a second component doing a
  subset of that job earns no capability this phase needs.
- **Piping MTTR/agent-decisions/LLM-cost through Prometheus (e.g. as
  Pushgateway metrics) instead of querying Postgres directly:** rejected
  — this data is inherently record-shaped (one row per incident/decision
  with real columns to filter and join on), and forcing it through a
  gauge format would throw away exactly the structure SQL already gives
  for free.
- **A fabricated real-looking webhook URL (Slack/PagerDuty) for the
  contact point:** rejected — would either silently fail (fake URL) or
  require inventing credentials for a service nobody set up, neither of
  which is honest about what's actually been verified.
- **Making this part of the production compose overlay
  (`docker-compose.prod.yml`):** rejected — these are operator tools, not
  public-facing, and ADR-0020 already settled that only Caddy belongs on
  the public surface of the production VM.
