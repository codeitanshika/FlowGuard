import sys
from pathlib import Path

# Payment's own client code lives under `app`, a package name every other
# service also uses — that name collision is exactly why these tests run
# as their own pytest invocation (`cd services/payment && python -m
# pytest`), never from the repo root alongside agents/shared tests. Needs
# both the repo root (for `shared`) and this service dir (for `app`) on
# sys.path, the same two entries the Dockerfile's /app layout gives for
# free by having both as siblings.
_SERVICE_ROOT = Path(__file__).resolve().parents[1]
_REPO_ROOT = _SERVICE_ROOT.parents[1]

for _path in (_REPO_ROOT, _SERVICE_ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))
