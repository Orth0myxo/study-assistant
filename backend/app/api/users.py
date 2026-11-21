from __future__ import annotations

import logging

from fastapi import APIRouter, Request, status

from app.schemas.users import (
    UserPreferences,
    UserPreferencesUpdate,
    UserRegistrationResponse,
)
from app.services.users import users

router = APIRouter(prefix="/api/v1", tags=["users"])
logger = logging.getLogger(__name__)


@router.post(
    "/users",
    response_model=UserRegistrationResponse,
    status_code=status.HTTP_201_CREATED,
)
def register_user() -> UserRegistrationResponse:
    """Create an anonymous device identity.

    This endpoint is reachable with the shared application token, but the
    returned bearer token (not the application token) is what identifies the
    user on data requests.
    """
    user_id, token = users.create()
    return UserRegistrationResponse(user_id=user_id, token=token)


@router.get("/users/preferences", response_model=UserPreferences)
def get_user_preferences(request: Request) -> UserPreferences:
    user_id = request.state.user_id
    consent = users.get_ai_reading_consent(user_id)
    logger.info(
        "ai_read consent=%s decision=settings_read remembered=%s pages=0 "
        "provider=- user_id=%s outcome=ok",
        consent,
        str(consent == "always").lower(),
        user_id,
    )
    return UserPreferences(ai_reading_scanned_pages=consent)


@router.put("/users/preferences", response_model=UserPreferences)
def update_user_preferences(
    payload: UserPreferencesUpdate, request: Request
) -> UserPreferences:
    user_id = request.state.user_id
    consent = users.set_ai_reading_consent(
        user_id, payload.ai_reading_scanned_pages
    )
    logger.info(
        "ai_read consent=%s decision=settings_set remembered=%s pages=0 "
        "provider=- user_id=%s outcome=updated",
        consent,
        str(consent == "always").lower(),
        user_id,
    )
    return UserPreferences(ai_reading_scanned_pages=consent)
