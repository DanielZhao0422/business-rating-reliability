"""Local HMAC seller pseudonyms without secret or identifier disclosure."""

from __future__ import annotations

import hashlib
import hmac
import os
import unicodedata
from collections.abc import Iterable


class PseudonymizationError(ValueError):
    """Raised when pseudonymization cannot be performed safely."""


SECRET_ENVIRONMENT_VARIABLE = "POWERBI_SELLER_KEY_SECRET"
_PLACEHOLDER_FRAGMENTS = (
    "changeme",
    "change_me",
    "password",
    "placeholder",
    "secret123",
    "your_secret",
    "example",
)


def validate_secret(secret: str | None) -> bytes:
    """Reject absent, empty, placeholder, repetitive, or short HMAC secrets."""

    if secret is None:
        raise PseudonymizationError(f"{SECRET_ENVIRONMENT_VARIABLE} is not configured")
    value = secret.strip()
    lowered = value.lower()
    if len(value) < 32:
        raise PseudonymizationError("seller pseudonymization secret is too short")
    if any(fragment in lowered for fragment in _PLACEHOLDER_FRAGMENTS):
        raise PseudonymizationError("seller pseudonymization secret is a placeholder")
    if len(set(value)) < 8 or len(set(value)) / len(value) < 0.2:
        raise PseudonymizationError("seller pseudonymization secret lacks diversity")
    return value.encode("utf-8")


def secret_from_environment() -> bytes:
    """Read and validate the local secret without logging its value."""

    return validate_secret(os.environ.get(SECRET_ENVIRONMENT_VARIABLE))


def normalize_seller_id(seller_id: str) -> str:
    """Apply the stable seller-ID normalization used before HMAC."""

    normalized = unicodedata.normalize("NFKC", seller_id).strip().lower()
    if not normalized:
        raise PseudonymizationError("seller identifier cannot be blank")
    return normalized


def pseudonymize_seller(seller_id: str, secret: bytes) -> str:
    """Return the approved 128-bit hexadecimal HMAC pseudonym."""

    digest = hmac.new(
        secret,
        normalize_seller_id(seller_id).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return digest[:32]


def pseudonymize_sellers(seller_ids: Iterable[str], secret: bytes) -> dict[str, str]:
    """Create a collision-checked mapping retained in memory only."""

    result: dict[str, str] = {}
    reverse: dict[str, str] = {}
    for source_id in seller_ids:
        key = pseudonymize_seller(source_id, secret)
        normalized = normalize_seller_id(source_id)
        if key in reverse and reverse[key] != normalized:
            raise PseudonymizationError("seller pseudonym collision detected")
        reverse[key] = normalized
        result[source_id] = key
    return result


def key_version(secret: bytes) -> str:
    """Return a nonreversible local key-version tag, never the key itself."""

    return hmac.new(
        secret, b"rating-reliability-powerbi-key-version-v1", hashlib.sha256
    ).hexdigest()[:16]
