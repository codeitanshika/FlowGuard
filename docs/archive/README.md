# Archived Docs

These were written during an earlier, lighter pass at this project — before
the API Gateway, the Monitor/Healer/Fraud Agent split, and the Ops
Controller (the allowlisted action executor) existed as named components.
They're kept for history, not deleted, but they are **not current** and
should not be used as a reference for the system as it's actually
designed. Each has a superseding doc in the main `docs/` tree:

| Archived file | Superseded by |
|---|---|
| `ARCHITECTURE.md` | [`architecture/`](../architecture/) (01–07, plus [decisions/](../decisions/)) |
| `BUILD_PLAN.md` | [`ROADMAP.md`](../ROADMAP.md) |
| `CODE_STANDARDS.md` | [`CODE_STANDARDS.md`](../CODE_STANDARDS.md) |
| `LIBRARY_DOCS.md` | [`LIBRARY_DOCS.md`](../LIBRARY_DOCS.md) |
| `PROJECT_OVERVIEW.md` | [`PROJECT_OVERVIEW.md`](../PROJECT_OVERVIEW.md) |
| `SETUP_GUIDE.md` | [`deployment/setup-guide.md`](../deployment/setup-guide.md) |
| `UNDERSTANDING.md` | [`UNDERSTANDING.md`](../UNDERSTANDING.md) |
| `UI_REGISTRY.md` | **Not superseded.** The current architecture has no dashboard/UI service — see note below. |

## Why `UI_REGISTRY.md` Has No Replacement

The old design included a `dashboard/` service with its own CSS design
tokens. The current architecture ([ROADMAP.md](../ROADMAP.md)) doesn't
include a custom operator dashboard — Phase 14's "dashboards" refers to
operational monitoring (e.g. Grafana/Jaeger), not a bespoke frontend. If a
custom UI is added later, it should get its own design doc written against
whatever's actually being built at that time, rather than reviving these
now-disconnected tokens.
