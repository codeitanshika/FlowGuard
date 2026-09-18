from shared.errors import ForbiddenError

_METHOD_TO_ACTION = {
    "GET": "read",
    "POST": "write",
    "PATCH": "write",
    "PUT": "write",
    "DELETE": "write",
}


def required_scope(method: str, resource: str) -> str:
    """Authorization: derives what a request needs (e.g. "payments:write")
    from its method and target resource. A valid, authenticated client
    (see security_deps.get_current_client) still needs the specific scope
    a route requires — authentication answers "who", this answers "is
    *this* client allowed to do *this*"."""

    action = _METHOD_TO_ACTION.get(method.upper(), "write")
    return f"{resource}:{action}"


def check_scope(client_scopes: list[str], required: str) -> None:
    if required not in client_scopes:
        raise ForbiddenError(f"missing required scope: {required}")
