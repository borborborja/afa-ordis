"""Canonical forms for account identifiers."""


def normalize_email(value: str | None) -> str:
    """Return the portal's case-insensitive representation of an email address."""
    return (value or "").strip().casefold()
