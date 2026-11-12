from app.services.db import db
from app.services.summary_cache import CachedAiClient, SummaryCache, summary_cache


def setup_function():
    summary_cache.clear()


def teardown_function():
    summary_cache.clear()


class CountingAi:
    def __init__(self, summary="cached summary"):
        self.calls = 0
        self.summary = summary

    def summarize(self, **kwargs):
        self.calls += 1
        return self.summary


def test_cache_hit_avoids_second_provider_call_and_counts_diagnostic():
    ai = CountingAi()
    first = CachedAiClient(ai, owner_id="user-a", retention_minutes=60)
    assert first.summarize(source_text=" A   source ", source_label="Page 1") == "cached summary"

    second = CachedAiClient(ai, owner_id="user-a", retention_minutes=60)
    assert second.summarize(source_text="a source", source_label="Page 9") == "cached summary"
    assert ai.calls == 1
    assert second.cache_hits == 1


def test_cache_isolated_per_user():
    cache = SummaryCache()
    ai = CountingAi()
    CachedAiClient(ai, owner_id="user-a", retention_minutes=60).summarize(source_text="source", source_label="p")
    other_user = CachedAiClient(ai, owner_id="user-b", retention_minutes=60)
    assert other_user.summarize(source_text="source", source_label="p") == "cached summary"
    assert ai.calls == 2
    assert other_user.cache_hits == 0
    cache.clear()


def test_prompt_version_change_invalidates_cache(monkeypatch):
    import app.services.summary_cache as cache_module

    ai = CountingAi()
    CachedAiClient(ai, owner_id="user-a", retention_minutes=60).summarize(source_text="source", source_label="p")
    monkeypatch.setattr(cache_module, "PROMPT_VERSION", "next-prompt")
    client = CachedAiClient(ai, owner_id="user-a", retention_minutes=60)
    client.summarize(source_text="source", source_label="p")
    assert ai.calls == 2
    assert client.cache_hits == 0


def test_expired_entries_are_misses(monkeypatch):
    import app.services.summary_cache as cache_module

    now = [1000.0]
    monkeypatch.setattr(cache_module.time, "time", lambda: now[0])
    cache = SummaryCache()
    ai = CountingAi()
    cache_client = CachedAiClient(ai, owner_id="user-a", retention_minutes=1)
    cache_client.summarize(source_text="source", source_label="p")
    now[0] += 61
    next_client = CachedAiClient(ai, owner_id="user-a", retention_minutes=1)
    next_client.summarize(source_text="source", source_label="p")
    assert ai.calls == 2


def test_sqlite_cache_migration_and_owner_key(tmp_path):
    db.configure(tmp_path / "cache.db")
    try:
        ai = CountingAi()
        CachedAiClient(ai, owner_id="user-a", retention_minutes=60).summarize(source_text="source", source_label="p")
        rows = db.query("SELECT owner_id, summary_text FROM summary_cache")
        assert [(row["owner_id"], row["summary_text"]) for row in rows] == [("user-a", "cached summary")]
        assert db.query("PRAGMA user_version")[0][0] == 8
    finally:
        db.reset()
