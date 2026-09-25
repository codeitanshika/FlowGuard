import hashlib
import uuid

# No real geolocation signal exists anywhere in this system — payment.created
# carries no IP or location. This simulates one deterministically, purely as
# a second, independent signal alongside velocity, so the Fraud Agent has a
# geo-anomaly code path to exercise (per docs/architecture/04-lld.md's
# `geo.py # simulated geo-anomaly check`) without inventing a geolocation
# subsystem that's out of scope. See ADR-0016.
#
# hashlib, not Python's built-in hash(): str hashing is randomized per
# process (PYTHONHASHSEED) unless disabled, which would make a user's home
# country change across restarts. hashlib is stable everywhere.
_COUNTRIES = ["US", "IN", "GB", "DE", "BR", "AU", "CA", "JP", "FR", "SG"]


def _stable_int(value: str) -> int:
    return int(hashlib.sha256(value.encode()).hexdigest(), 16)


def home_country(user_id: uuid.UUID) -> str:
    """A user's simulated "usual" country — a fixed function of their id,
    not stored anywhere, so it's consistent across Fraud Agent restarts
    with no state to lose."""
    return _COUNTRIES[_stable_int(str(user_id)) % len(_COUNTRIES)]


def is_geo_anomaly(
    user_id: uuid.UUID, transaction_id: uuid.UUID, mismatch_denominator: int = 10
) -> tuple[bool, str, str]:
    """Returns (anomaly, home_country, observed_country). Roughly 1 in
    `mismatch_denominator` transactions are simulated as originating
    somewhere other than the user's home country — deterministic per
    transaction_id, so a given transaction always produces the same
    verdict (useful for tests and for reproducing a specific incident)."""

    home = home_country(user_id)
    if _stable_int(f"{transaction_id}:trigger") % mismatch_denominator != 0:
        return False, home, home

    offset = 1 + (_stable_int(f"{transaction_id}:offset") % (len(_COUNTRIES) - 1))
    observed = _COUNTRIES[(_COUNTRIES.index(home) + offset) % len(_COUNTRIES)]
    return True, home, observed
