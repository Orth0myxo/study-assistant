"""Shared server-side consent checks for page-image AI reading."""

from __future__ import annotations

import logging
from typing import Literal

from app.config import Settings
from app.errors import ApiError
from app.services.users import users

logger = logging.getLogger(__name__)

AiReadDecision = Literal["allow_once", "remember", "deny"] | None


def check_ai_read_consent(
    owner_id: str,
    decision: AiReadDecision,
    settings: Settings,
    *,
    page_count: int,
    resource_id: str = "-",
) -> bool:
    """Return True only when reading is authorized; False means ask first.

    The persisted ``never`` choice has priority over request decisions. A
    request decision is supplied only by a user-facing control, never by an
    agent tool argument.
    """
    preference = users.get_ai_reading_consent(owner_id)
    provider = str(settings.image_reading_provider or "").strip().lower() or "unknown"

    if preference == "never":
        _log(preference, "deny", True, page_count, provider, resource_id, "blocked")
        raise ApiError(
            "ai_read_never_allowed",
            "AI reading of scanned pages is turned off in Settings.",
            403,
        )

    if decision == "deny":
        _log(
            preference,
            "deny",
            preference == "always",
            page_count,
            provider,
            resource_id,
            "declined",
        )
        raise ApiError("ai_read_declined", "AI reading was not started.", 403)

    if decision == "remember":
        users.set_ai_reading_consent(owner_id, "always")
        _log("always", "allow", True, page_count, provider, resource_id, "allowed")
        return True

    if decision == "allow_once":
        _log(preference, "allow", False, page_count, provider, resource_id, "allowed")
        return True

    if preference == "always":
        _log(preference, "allow", True, page_count, provider, resource_id, "allowed")
        return True

    _log(
        preference,
        "ask",
        False,
        page_count,
        provider,
        resource_id,
        "needs_approval",
    )
    return False


def _log(
    consent: str,
    decision: str,
    remembered: bool,
    page_count: int,
    provider: str,
    resource_id: str,
    outcome: str,
) -> None:
    logger.info(
        "ai_read consent=%s decision=%s remembered=%s pages=%d provider=%s "
        "resource_id=%s outcome=%s",
        consent,
        decision,
        str(remembered).lower(),
        page_count,
        provider,
        resource_id,
        outcome,
    )
