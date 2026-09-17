class AppError(Exception):
    """Base for every domain error. Carries the shared error vocabulary
    (see docs/api/api-contracts.md) so a single exception handler can turn
    any of these into the correct envelope + HTTP status."""

    def __init__(self, code: str, message: str, status_code: int) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class ValidationAppError(AppError):
    def __init__(self, message: str) -> None:
        super().__init__("VALIDATION_ERROR", message, 400)


class UnauthorizedError(AppError):
    def __init__(self, message: str = "authentication required") -> None:
        super().__init__("UNAUTHORIZED", message, 401)


class ForbiddenError(AppError):
    def __init__(self, message: str = "not permitted") -> None:
        super().__init__("FORBIDDEN", message, 403)


class NotFoundError(AppError):
    def __init__(self, message: str) -> None:
        super().__init__("NOT_FOUND", message, 404)


class ConflictError(AppError):
    def __init__(self, message: str) -> None:
        super().__init__("CONFLICT", message, 409)


class RateLimitedError(AppError):
    def __init__(self, message: str = "rate limit exceeded") -> None:
        super().__init__("RATE_LIMITED", message, 429)


class DependencyUnavailableError(AppError):
    def __init__(self, message: str) -> None:
        super().__init__("DEPENDENCY_UNAVAILABLE", message, 503)
