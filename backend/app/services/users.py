from __future__ import annotations

import hashlib
import secrets
import threading
import time
import uuid

from app.services.db import db


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

    def clear(self) -> None:
        with self._lock:
            self._memory.clear()
        if db.configured:
            db.execute("DELETE FROM users")


users = UserStore()
