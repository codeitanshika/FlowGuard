import pytest

from tests.live_helpers import StackUnavailable, check_stack_is_up, login


@pytest.fixture(scope="session", autouse=True)
def _require_stack():
    try:
        check_stack_is_up()
    except StackUnavailable as exc:
        pytest.skip(str(exc))


@pytest.fixture(scope="session")
def merchant_token() -> str:
    return login()
