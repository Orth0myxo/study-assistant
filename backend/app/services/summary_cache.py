from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from typing import Any

from app.services.ai_client import AiClient, PROMPT_VERSION
from app.services.db import db


def _key_hash(purpose: str, source_text: str, beyond_source: bool) -> str:
    normalized = re.sub(r"\s+", " ", source_text).strip().casefold()
    payload = json.dumps(
        [purpose, normalized, PROMPT_VERSION, bool(beyond_source)],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class SummaryCache:
    def __init__(self) -> None:
        self._memory: dict[tuple[str, str], tuple[str, float]] = {}
        self._lock = threading.RLock()

    def get(self, owner_id: str | None, key_hash: str, retention_minutes: int) -> str | None:
        if not owner_id:
            return None
        cutoff = time.time() - max(retention_minutes, 0) * 60
        if db.configured:
            db.execute("DELETE FROM summary_cache WHERE created_at < ?", (cutoff,))
            rows = db.query(
                "SELECT summary_text FROM summary_cache WHERE owner_id = ? AND key_hash = ? AND created_at >= ?",
                (owner_id, key_hash, cutoff),
            )
            return str(rows[0]["summary_text"]) if rows else None
        with self._lock:
            entry = self._memory.get((owner_id, key_hash))
            if entry is None or entry[1] < cutoff:
                self._memory.pop((owner_id, key_hash), None)
                return None
            return entry[0]

    def put(self, owner_id: str | None, key_hash: str, summary: str) -> None:
        if not owner_id or not summary:
            return
        created_at = time.time()
        if db.configured:
            db.execute(
                "INSERT OR REPLACE INTO summary_cache (owner_id, key_hash, summary_text, created_at) VALUES (?, ?, ?, ?)",
                (owner_id, key_hash, summary, created_at),
            )
            return
        with self._lock:
            self._memory[(owner_id, key_hash)] = (summary, created_at)

    def clear(self) -> None:
        with self._lock:
            self._memory.clear()
        if db.configured:
            db.execute("DELETE FROM summary_cache")


class CachedAiClient:
    def __init__(self, client: AiClient, *, owner_id: str | None, retention_minutes: int) -> None:
        self._client = client
        self._owner_id = owner_id
        self._retention_minutes = retention_minutes
        self.cache_hits = 0

    @property
    def hits(self) -> int:
        return self.cache_hits

    def summarize(self, *, source_text: str, source_label: str, purpose: str = "section") -> str:
        key_hash = _key_hash(purpose, source_text, purpose == "topic_beyond")
        cached = summary_cache.get(self._owner_id, key_hash, self._retention_minutes)
        if cached is not None:
            self.cache_hits += 1
            return cached
        summary = self._client.summarize(source_text=source_text, source_label=source_label, purpose=purpose)
        summary_cache.put(self._owner_id, key_hash, summary)
        return summary


summary_cache = SummaryCache()
