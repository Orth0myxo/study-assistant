from __future__ import annotations

from pydantic import BaseModel


class UserRegistrationResponse(BaseModel):
    user_id: str
    token: str
