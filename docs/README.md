# FlowGuard Docs

Single entry point into the docs. Everything here is aligned to the Phase
0 architecture (Gateway + Payment/Fraud/User/Notification services,
Monitor/Healer/Fraud agents, Ops Controller as the AI-safety boundary) —
if a doc contradicts this set, it's stale and should be fixed or archived.

## Start Here

- [ROADMAP.md](ROADMAP.md) — the 15-phase build plan and current status
- [PROJECT_OVERVIEW.md](PROJECT_OVERVIEW.md) — problem, solution, features, positioning
- [UNDERSTANDING.md](UNDERSTANDING.md) — plain-English explanation of every core concept

## System Design (Phase 0)

- [architecture/](architecture/) — requirements, HLD, LLD, service boundaries, database schema, event flows, failure scenarios
- [api/api-contracts.md](api/api-contracts.md) — endpoint contracts for every service
- [decisions/](decisions/) — ADRs: every major trade-off and why it was made

## Building

- [CODE_STANDARDS.md](CODE_STANDARDS.md) — folder structure, Python/API/agent/git/Docker conventions
- [LIBRARY_DOCS.md](LIBRARY_DOCS.md) — what each core dependency is and how it's used here
- [deployment/setup-guide.md](deployment/setup-guide.md) — local environment and Docker Compose (grows into cloud deployment in Phase 13)
- [security/](security/) — security decisions made so far, and what Phase 2/12 will add

## History

- [archive/](archive/) — superseded docs from an earlier, lighter pass at this project, kept for reference, not current
