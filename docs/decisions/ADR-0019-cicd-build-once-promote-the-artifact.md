# ADR-0019: CI/CD Builds Once and Promotes the Artifact; One Tool for Style; Every Gate Checked Before It Was Wired In

## Status
Accepted

## Context
Phase 12 needs a GitHub Actions pipeline covering lint, format, tests,
security checks, Docker builds, and a staging/production path. Two
constraints shaped it. First, the pipeline can't be run on GitHub's
runners from where it was built, so the risk of a workflow that is
correct in isolation but red on day one is real. Second, Phase 13 (cloud
deployment) hasn't happened, so there is no environment to deploy *to*.

## Decision

**1. Every check was run locally against the real codebase before it was
made a gate.** A gate that starts red teaches everyone to ignore it. So
ruff, bandit, pip-audit, gitleaks (full history, 73 commits) and Trivy
were each run first, findings triaged, and the workflow written against
what actually passes. That process found things a paper design would not
have:
   - `requirements-dev.txt` — the file CI's test jobs install — was
     missing `anthropic` (which brings `httpx2`), so unit tests would not
     even have *collected* on a clean runner. It had gone unnoticed for
     four phases because the developer virtualenv had accreted packages.
     Found by building a fresh venv from only that file.
   - A test (`test_low_risk_writes_nothing_and_freezes_nobody`) failed
     ~10% of runs: it used a random UUID, and the simulated geo signal
     ([ADR-0016](ADR-0016-fraud-agent-simulation-and-freeze-cooldown.md))
     fires for ~1 in 10 of them. I had previously waved off its first
     failure as load. A red build in CI at that rate would have taught the
     opposite lesson from the one a gate exists to teach.
   - 98 initial lint findings, triaged rather than blanket-suppressed:
     the 39 `B008` were FastAPI's `Depends()` default-argument idiom
     (configured as immutable, not disabled); two rules are ignored with
     written reasons (`UP042`: `StrEnum` would change `str()` behaviour
     of persisted/serialized enums; `UP046`: PEP 695 generics, churn
     only); the LLM-prompt files are exempt from line length so the exact
     prompt text is never re-wrapped; blocking `time.sleep` in async
     chaos-test helpers was a real fix.

**2. Ruff does both linting and formatting.** The Phase 0 standard said
"ruff + black". Ruff's formatter is black-compatible and one tool means
one version, one config (`ruff.toml`), one CI step. The formatting
change was applied as its own commit so `git blame --ignore-rev` can
skip it.

**3. Build once, verify that artifact, promote that artifact.** On a push
to main, once every gate has passed, CI publishes the nine images to GHCR
tagged `sha-<commit>`; the *staging* job then runs the integration suite
against those published images (`docker-compose.images.yml`, `up
--no-build`), not against a fresh build. Pushing a `v*` tag re-tags that
same image as the release and as `production` — never a rebuild — and
refuses if no `sha-<commit>` image exists, meaning that commit never
passed CI. What was tested is byte-for-byte what ships.

**4. The production gate is a GitHub Environment, not code.** The
`production` environment must be configured with required reviewers in
the repository settings; that manual approval is the gate. It cannot be
enforced from the workflow file — which is why the setup guide lists it as
a required step rather than pretending the YAML does it.

**5. Nightly, not per-PR, for chaos.** The MTTR tests
([ADR-0018](ADR-0018-chaos-testing-measures-real-mttr.md)) are
timing-based and take minutes; they run on a schedule and on demand,
uploading the MTTR report.

**6. The "deploy" step is deliberately a notice.** There is no deployment
target until Phase 13. The workflow promotes images and then says so,
rather than containing a step that appears to deploy and does nothing.

## Consequences
- **Known gap — the workflows have not run on GitHub.** They pass
  `actionlint` and the published GitHub-workflow and Dependabot JSON
  schemas, and each command was exercised locally (fresh-venv install,
  `.env.example` stack + integration suite, `--wait`, the registry-image
  override with locally tagged images, gitleaks, Trivy, bandit,
  pip-audit). What that cannot cover: GHCR push/pull permissions, the
  buildx layer cache, runner-specific behaviour. The first run on GitHub
  should be watched, and failures there are expected to be
  environment-shaped rather than logic-shaped.
- **Known gap — dependencies are unpinned.** Every service's
  `pyproject.toml` uses `>=` ranges and there are no lockfiles, so an
  image built next week can differ from one built today, and pip-audit
  audits whatever resolves *now*. Reproducible builds need lockfiles;
  that is future work, and the "build once, promote" design is what
  contains the risk (the artifact that passed is the artifact that ships)
  until then.
- **Known gap — actions are pinned by major-version tag, not commit
  SHA.** Dependabot proposes updates; SHA-pinning would be stricter
  against a compromised tag.
- **Known gap — Trivy passing today is a point-in-time fact.** It gates on
  *fixable* HIGH/CRITICAL findings (`--ignore-unfixed`), so a new
  fixable CVE in the base image can turn a build red without any code
  change — which is the intended behaviour, and a rebuild after the base
  image updates clears it.
- **Accepted cost:** publishing rebuilds each image after the scan job
  built it (layers come from the GitHub Actions cache, so it is quick, but
  it is a second build step rather than pushing the scanned image).
- **Benefit:** a red main means something specific, and a green one means
  every gate — including a full-stack integration run against the
  published artifacts — passed for that commit.

## Alternatives Considered
- **Rebuild per environment** (build in staging, build again for
  production): rejected — the classic way for "it passed staging" to stop
  being true of what actually ships.
- **A third-party secret-scanning action:** rejected in favour of the
  pinned, checksum-verified gitleaks binary — the hosted action needs a
  licence for organisation repositories, and a downloaded binary whose
  SHA-256 is verified has one fewer moving part.
- **Gating every PR on the chaos tests:** rejected — minutes of
  timing-dependent runtime on every push would trade a stable signal for
  a slow one.
- **A placeholder deploy step that echoes "deploying...":** rejected —
  the pipeline should not claim a rollout that doesn't exist.
