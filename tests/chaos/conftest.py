import pytest

from tests.chaos.db import check_control_db_is_reachable
from tests.live_helpers import StackUnavailable, check_stack_is_up, login


@pytest.fixture(scope="session", autouse=True)
def _require_stack():
    try:
        check_stack_is_up()
    except StackUnavailable as exc:
        pytest.skip(str(exc))


@pytest.fixture(scope="session", autouse=True)
async def _require_control_db():
    try:
        await check_control_db_is_reachable()
    except Exception as exc:  # noqa: BLE001 - any connection failure means the same thing here
        pytest.skip(
            "flowguard_control isn't reachable on localhost:5432 — chaos tests need the test "
            "compose override that publishes it: `docker compose -f docker-compose.yml -f "
            f"infra/docker/docker-compose.test.yml up --build -d` ({exc})"
        )


@pytest.fixture(scope="session")
def merchant_token() -> str:
    return login()
