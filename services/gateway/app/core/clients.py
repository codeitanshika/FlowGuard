from pydantic import BaseModel


class ClientCredential(BaseModel):
    """One configured API client. See ADR-0010 for why this is static
    config rather than a database-backed client-management system."""

    secret_hash: str
    scopes: list[str]
