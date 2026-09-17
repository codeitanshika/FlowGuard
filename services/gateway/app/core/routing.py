from app.core.config import Settings


def build_route_table(settings: Settings) -> dict[str, str]:
    """Maps the first path segment after /api/v1/ to a backend base URL.
    Deliberately does not list "internal" — see docs/architecture/03-service-boundaries.md:
    /internal/* routes are never reachable through the Gateway, and since
    this table only recognizes the three public segments below, any
    /api/v1/internal/... request simply has no match and 404s. Fraud
    Service isn't listed either — it's never called by clients directly,
    only by Payment Service internally."""

    return {
        "payments": settings.payment_service_url,
        "users": settings.user_service_url,
        "notifications": settings.notification_service_url,
    }
