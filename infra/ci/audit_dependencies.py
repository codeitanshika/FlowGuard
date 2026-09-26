"""Audits every dependency any service or agent declares for known
vulnerabilities (pip-audit against the PyPI advisory database).

Each service/agent has its own pyproject.toml listing exactly what its
Docker image installs (see docs/CODE_STANDARDS.md), so this reads those —
the real production dependency set — rather than the developer-convenience
requirements-dev.txt. Run from the repo root:

    python infra/ci/audit_dependencies.py
"""

import subprocess  # nosec B404 - fixed argv only, see the call below
import sys
import tempfile
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def collect_dependencies() -> dict[str, list[str]]:
    """requirement string -> which manifests declare it."""
    found: dict[str, list[str]] = {}
    for manifest in sorted(REPO_ROOT.glob("*/*/pyproject.toml")):
        data = tomllib.loads(manifest.read_text(encoding="utf-8"))
        for requirement in data.get("project", {}).get("dependencies", []):
            found.setdefault(requirement, []).append(str(manifest.parent.relative_to(REPO_ROOT)))
    return found


def main() -> int:
    dependencies = collect_dependencies()
    if not dependencies:
        print("no service/agent pyproject.toml dependencies found — nothing to audit", file=sys.stderr)
        return 2

    manifests = {manifest for declared_by in dependencies.values() for manifest in declared_by}
    print(f"auditing {len(dependencies)} distinct requirements from {len(manifests)} manifests")
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as handle:
        handle.write("\n".join(sorted(dependencies)) + "\n")
        requirements_path = handle.name

    # --strict: fail if any dependency can't be audited, rather than silently
    # skipping it and reporting a clean result.
    argv = [sys.executable, "-m", "pip_audit", "-r", requirements_path, "--strict"]
    return subprocess.call(argv)  # nosec B603 - no shell, no user input: interpreter + literal args + a temp file


if __name__ == "__main__":
    raise SystemExit(main())
