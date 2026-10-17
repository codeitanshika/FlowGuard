# ADR-0020: A Single VM Behind Caddy, Terraform Built but Not Applied, Secrets Never Through env_file

## Status
Accepted

## Context
Phase 13 needs "simple architecture (no Kubernetes), networking, secrets,
HTTPS, rollback strategy." Two decisions were the user's to make, not
mine: which cloud shape, and whether to actually provision real, billed
infrastructure in this session. They chose a single VM running the same
Docker Compose stack this project already has, and chose to have
everything built and locally validated but not applied — no real cloud
account exists to provision into right now, and `terraform apply` creates
real, billed resources that only the account owner should authorize.

## Decision

**1. One EC2 instance, not ECS/Fargate or a managed PaaS.** The whole
project is already Docker-Compose-first; a single VM running the exact
same `docker-compose.yml` (plus two small overlays) is the smallest
possible delta from everything already built, and "no Kubernetes" reads
most naturally as "no orchestrator at all" rather than "a lighter
orchestrator." Terraform provisions the VM and installs Docker
(`cloud-init.yaml.tftpl`) and nothing else — it never runs `docker compose
up`. Deploying is a separate, repeatable operation
(`infra/scripts/deploy.sh`, over SSH) so a later release or rollback never
touches Terraform at all.

**2. Every check that doesn't require a real cloud account was run for
real** — the same discipline as ADR-0019, applied to infrastructure this
time instead of CI. `terraform fmt`, `init` (downloads the AWS provider
from the public registry; needs network, not AWS credentials), and
`validate` all ran and caught a real error (AWS security-group
descriptions reject apostrophes). A `plan` with placeholder variables got
as far as calling AWS's STS API before failing on dummy credentials —
confirming the resource graph itself is sound, not just syntactically
valid. The Caddyfile was validated with the real `caddy validate` binary.
The production compose overlay was rendered with `docker compose config`
*and* actually run: local images tagged as a fake registry, the full
merged stack brought up, and a real login → create-user → authenticated-
request round trip completed over HTTPS through Caddy (a temporary `tls
internal` swap for that one local test, since there is no real domain to
get a Let's Encrypt certificate for; the committed Caddyfile uses real
ACME and was re-validated after reverting). `terraform apply` is the one
thing deliberately never run.

**3. Compose's list-merge semantics would have silently exposed every
internal service to the internet.** `docker-compose.yml` publishes every
service's port for local-dev convenience; Compose merges a `ports:` list
across `-f` files by *appending*, not replacing, so an overlay that
simply never mentions `ports:` leaves the base file's ports published
too. Found by actually rendering the merged config (`docker compose
config`) and seeing eleven `published:` entries where there should have
been one. Fixed with the Compose Specification's `!reset` tag
(`ports: !reset []`), confirmed against the installed Compose (v5.3.1)
with an isolated test before relying on it — the alternative, silently
shipping every service's unauthenticated internal endpoints (User's
freeze/debit, Payment's circuit-breaker control, fault injection) to the
public internet, is exactly the kind of mistake this ADR exists to
prevent. Verified on real running containers afterward
(`docker compose ps`): only `caddy` has a host-published port.

**4. Secrets flow through `${VAR}` substitution, never `env_file:`.**
Verified empirically (not from memory of the Phase 3 `$`-escaping
incident, which was a different mechanism): a value sourced via
`env_file:` containing a `$word`-shaped substring is silently truncated
by Compose's own env-file parser, but the identical value substituted via
`${VAR}` from an externally-exported shell variable passes through
completely unmangled, no escaping needed at all. `infra/scripts/
deploy.sh` `source`s the production secrets file with `set -a` before
running Compose, which is what makes every secret — `GATEWAY_CLIENTS`'s
bcrypt hashes included — exactly such a variable. Confirmed on a real
running container: `GATEWAY_CLIENTS` arrived with its real, single-`$`
hash intact. `POSTGRES_PASSWORD` is new and required this way —
`docker-compose.yml` hardcodes a weak one (`flowguard`/`flowguard`) for
local dev, which the production overlay replaces rather than inherits.

**5. Fronting the Gateway with a reverse proxy breaks its IP-based rate
limiter, unless told to trust the proxy.** Found while designing this
phase, not reported by a user: every request's `request.client.host`
becomes Caddy's own container IP, and the login rate limiter
(`app/api/auth.py`) keys exactly on that. `GATEWAY_TRUST_PROXY_HEADERS`
(default `false` — zero behavior change for local dev or any existing
test) wraps the assembled ASGI app in uvicorn's own
`ProxyHeadersMiddleware`, trusting `X-Forwarded-For` unconditionally —
verified safe specifically because nothing in the production topology
can reach the Gateway except Caddy (no published port of its own). Proven
with a real test using a real Redis, not just inspection: one test shows
the bug (two different forwarded IPs sharing one rate-limit bucket with
the setting off), the other shows the fix (each gets its own bucket with
it on).

**6. Rollback is the same script as deploy, parameterized by tag.**
`infra/scripts/deploy.sh <tag> <domain>` is called identically by
`promote.yml`'s deploy job (a new release) and the new `rollback.yml`
(reverting to an older one) — a rollback is not a conceptually different
operation, so it isn't a different code path. Both require a tag that
already exists in the registry; neither ever rebuilds.

**7. The deploy script's own bugs were caught by running it, not by
reading it.** `bash -n` and a downloaded, checksum-unverified-but-pinned
`shellcheck` binary both ran against it before it was trusted. Two real
findings: a bare apostrophe inside a `${VAR:?message}` construct breaks
bash's internal quote-balancing even though the whole thing sits inside
double quotes — not a style nit, a flat parse failure; and the file had
picked up CRLF line endings from this Windows checkout's own `autocrlf`
conversion, which is a hard syntax error for a shebang script. Both
fixed; `.gitattributes` (`*.sh text eol=lf`) added so the second one
cannot recur for this or any future script.

**8. A relative bind-mount path resolves against the *project*
directory, not the file that declares it.** `../../Caddyfile` in
`infra/docker/docker-compose.prod.yml` landed two directories above the
repo root instead of at it. Found with an isolated two-file Compose test
before touching the real stack, fixed to `./Caddyfile` (correct because
Compose's project directory is the first `-f` file's directory — repo
root, since `docker-compose.yml` is always listed first).

## Consequences
- **Known gap — nothing in this phase has run against a real cloud
  account.** `apply`, the real SSH deploy, the real Let's Encrypt
  certificate issuance, and GHCR pull permissions on an actual VM are all
  unverified. The first real `apply` and first real deploy should be
  watched closely; failures there are expected to be account/DNS-shaped,
  not logic-shaped — the same posture as ADR-0019's CI workflows before
  their first real run.
- **Known gap — no backups, no HA, no load testing.** Postgres's data
  lives on the instance's own root volume; losing the instance loses the
  data (mitigated only by `prevent_destroy` on the Terraform resource,
  not by any actual backup). `t3.medium` is sized by reasoning about the
  container count, not measured under load. A single VM is a single
  point of failure by construction — matches this project's existing
  "portfolio scale, not production scale" framing (`docs/PROJECT_OVERVIEW.md`),
  stated plainly rather than hidden.
- **Known gap — the production secrets file's shell-quoting requirement
  is a real footgun.** A value containing a literal `$` (any bcrypt hash)
  must be single-quoted in `PRODUCTION_ENV_FILE`, unlike the local
  `.env`/`.env.example` format, which pydantic-settings reads without any
  shell-quoting rules at all. `deploy.sh` runs `bash -n` on it before
  copying it anywhere, which catches a quoting mistake with a clear error
  rather than a cryptic failure mid-deploy — but the two file formats
  looking almost identical while needing different quoting rules is a
  real, documented trap (see the setup guide).
- **Benefit:** every secret-handling and ports-exposure decision in this
  phase was forced to be explicit and was checked against a real running
  container, not assumed from the design. Two of this ADR's eight
  decisions (3 and 5) are bugs that would have shipped — one a direct
  security exposure of every internal service, the other a production
  rate-limiting failure — if this phase had stopped at "write the YAML
  and the ADR" the way a time-constrained pass might.

## Alternatives Considered
- **AWS ECS on Fargate:** rejected for this phase — a VPC, an ALB, and a
  task/service definition per container is meaningfully more
  infrastructure and cost than "simple architecture" asked for, and none
  of it reuses what already exists the way a plain VM does.
- **A managed PaaS (Render/Railway/Fly.io):** rejected — least
  infrastructure to build, but secrets and networking work differently
  per platform rather than through portable Terraform, and there is
  markedly less "real cloud architecture" to demonstrate.
- **Overlay-only ports fix via omission:** considered and empirically
  disproven (decision 3) — Compose's append semantics for lists make this
  actively dangerous, not just imperfect.
- **SSH trust-on-first-connect (no pinned host key):** rejected —
  `deploy.sh` refuses to run without `DEPLOY_HOST_KEY` rather than falling
  back to it, the same "fail fast rather than silently insecure" posture
  as every no-default secret in this codebase.
- **Provisioning real infrastructure in this session:** explicitly
  declined by the user, and the right call regardless — `apply` creates
  billed resources that should be a deliberate, reviewed action by the
  account owner, not something run unilaterally.
