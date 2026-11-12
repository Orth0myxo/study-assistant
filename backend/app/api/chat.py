from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from fastapi import APIRouter, Request

from app.config import get_settings
from app.errors import ApiError
from app.schemas.chat_command import ChatCommand, ChatCommandRequest
from app.services.chat_commands import resolve_command

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])
_requests: dict[str, deque[float]] = defaultdict(deque)
_lock = threading.Lock()


def _enforce_rate_limit(user_id: str, limit: int) -> None:
    now = time.monotonic()
    with _lock:
        recent = _requests[user_id]
        while recent and now - recent[0] >= 60:
            recent.popleft()
        if len(recent) >= max(limit, 1):
            raise ApiError("rate_limit_exceeded", "Too many chat commands. Please wait a moment and try again.", 429)
        recent.append(now)


@router.post("/command", response_model=ChatCommand)
def chat_command(payload: ChatCommandRequest, request: Request) -> ChatCommand:
    settings = get_settings()
    _enforce_rate_limit(request.state.user_id, settings.rate_limit_per_minute)
    return resolve_command(payload, settings)
