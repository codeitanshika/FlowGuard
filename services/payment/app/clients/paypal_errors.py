import httpx

# PayPal's standard REST error envelope across every API:
# {"name": "UNPROCESSABLE_ENTITY", "message": "...", "debug_id": "...",
#  "details": [{"issue": "ORDER_NOT_APPROVED", "description": "..."}], "links": [...]}
# See https://developer.paypal.com/api/rest/responses/.

# Issues that mean "PayPal understood the request and declined this specific
# payment" — a business outcome, the same tier as MockPaymentProvider's
# amount==0.13 decline. Everything else (auth failure, malformed request,
# rate limit, 5xx, network error) is a dependency failure and should count
# toward the provider circuit breaker.
_DECLINE_ISSUES = frozenset(
    {
        "ORDER_NOT_APPROVED",
        "INSTRUMENT_DECLINED",
        "PAYER_ACTION_REQUIRED",
        "TRANSACTION_REFUSED",
        "DUPLICATE_INVOICE_ID",
        "AMOUNT_MISMATCH",
    }
)


class PayPalDecline(Exception):
    """PayPal declined this specific payment — not an outage. Carries the
    issue code and a human-readable reason for ProviderResult.failure_reason."""

    def __init__(self, issue: str, reason: str) -> None:
        self.issue = issue
        self.reason = reason
        super().__init__(reason)


def parse_error(response: httpx.Response) -> tuple[str | None, str]:
    """Returns (issue_or_None, human_readable_message) from a PayPal error
    response. Never raises — an unparseable body just means issue=None,
    which callers treat as a dependency failure, the safer default."""

    try:
        body = response.json()
    except ValueError:
        return None, f"PayPal returned {response.status_code}: {response.text[:200]}"

    details = body.get("details") or []
    issue = details[0].get("issue") if details else None
    description = details[0].get("description") if details else None
    message = description or body.get("message") or f"PayPal returned {response.status_code}"
    return issue, message


def classify(response: httpx.Response) -> PayPalDecline | None:
    """None means "treat as a dependency failure"; a PayPalDecline means
    "this is a business decline, raise it as one"."""
    issue, message = parse_error(response)
    if issue in _DECLINE_ISSUES:
        return PayPalDecline(issue, message)
    return None
