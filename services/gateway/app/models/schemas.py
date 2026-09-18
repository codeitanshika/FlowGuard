from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    client_id: str = Field(min_length=1)
    client_secret: str = Field(min_length=1)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class AuthenticatedClient(BaseModel):
    client_id: str
    scopes: list[str]
