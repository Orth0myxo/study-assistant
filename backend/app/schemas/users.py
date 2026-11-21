from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class UserRegistrationResponse(BaseModel):
    user_id: str
    token: str


class UserPreferences(BaseModel):
    ai_reading_scanned_pages: Literal["ask", "always", "never"] = "ask"


class UserPreferencesUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ai_reading_scanned_pages: Literal["ask", "always", "never"]
