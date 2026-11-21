from __future__ import annotations

import hashlib
import secrets
import threading
import time
import uuid
from typing import Literal

from app.services.db import db

AiReadingConsent = Literal["ask", "always", "never"]
_AI_READING_CONSENTS = frozenset({"ask", "always", "never"})


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class UserStore:
    """Anonymous device accounts for the beta.

    The server issues an opaque bearer token per installation and stores only
    its SHA-256 hash. The shared application token is never used as identity.
    A small in-memory map keeps isolated unit tests working when no database
    is configured; with SQLite the hashes are the only stored credential.
    """

    def __init__(self) -> None:
        self._memory: dict[str, str] = {}
        self._ai_reading: dict[str, AiReadingConsent] = {}
        self._lock = threading.Lock()

    def create(self) -> tuple[str, str]:
        user_id = uuid.uuid4().hex
        token = secrets.token_urlsafe(32)
        digest = hash_token(token)
        with self._lock:
            self._memory[digest] = user_id
        if db.configured:
            db.execute(
                "INSERT INTO users (user_id, token_hash, created_at) "
                "VALUES (?, ?, ?)",
                (user_id, digest, time.time()),
            )
        return user_id, token

    def resolve(self, token: str | None) -> str | None:
        if not token or len(token) > 512:
            return None
        digest = hash_token(token)
        if db.configured:
            rows = db.query(
                "SELECT user_id FROM users WHERE token_hash = ?", (digest,)
            )
            return rows[0]["user_id"] if rows else None
        with self._lock:
            return self._memory.get(digest)

    def get_ai_reading_consent(self, user_id: str) -> AiReadingConsent:
        if db.configured:
            rows = db.query(
                "SELECT ai_reading_scanned_pages FROM users WHERE user_id = ?",
                (user_id,),
            )
            if rows:
                value = str(rows[0]["ai_reading_scanned_pages"] or "ask")
                if value in _AI_READING_CONSENTS:
                    return value  # type: ignore[return-value]
        with self._lock:
            value = self._ai_reading.get(user_id, "ask")
        return value

    def set_ai_reading_consent(
        self, user_id: str, consent: AiReadingConsent
    ) -> AiReadingConsent:
        if consent not in _AI_READING_CONSENTS:
            raise ValueError("invalid AI reading consent preference")
        if db.configured:
            db.execute(
                "UPDATE users SET ai_reading_scanned_pages = ? WHERE user_id = ?",
                (consent, user_id),
            )
        with self._lock:
            self._ai_reading[user_id] = consent
        return consent

    def clear(self) -> None:
        with self._lock:
            self._memory.clear()
            self._ai_reading.clear()
        if db.configured:
            db.execute("DELETE FROM users")


users = UserStore()
