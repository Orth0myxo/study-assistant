from __future__ import annotations

from fastapi import APIRouter, status

from app.schemas.users import UserRegistrationResponse
from app.services.users import users

router = APIRouter(prefix="/api/v1", tags=["users"])


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
