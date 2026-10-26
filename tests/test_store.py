import json
from datetime import datetime, timedelta, timezone

from agent import store

NOW = datetime(2026, 10, 26, 3, 17, tzinfo=timezone.utc)
LOOKBACK = {"first_run_hours": 24, "overlap_minutes": 30}


def test_write_json_keeps_korean_readable(tmp_path):
    path = tmp_path / "a.json"
    store.write_json(path, {"제목": "반도체"})
    raw = path.read_text(encoding="utf-8")
    assert "반도체" in raw and "\\u" not in raw
    assert json.loads(raw) == {"제목": "반도체"}
    assert list(tmp_path.iterdir()) == [path]  # 임시 파일이 남지 않음


def test_collect_since():
    assert store.collect_since({"last_success_at": None}, LOOKBACK, NOW) == NOW - timedelta(hours=24)
    state = {"last_success_at": "2026-10-26T00:17:00Z"}
    assert store.collect_since(state, LOOKBACK, NOW) == datetime(2026, 10, 25, 23, 47, tzinfo=timezone.utc)


def test_seen_marking_and_pruning():
    state = store.load_state(store.Path("does-not-exist"))
    store.mark_seen(state, ["old"], NOW - timedelta(days=20))
    store.mark_seen(state, ["new"], NOW)
    store.prune_seen(state, days=14, now=NOW)
    assert set(state["seen"]) == {"new"}


def test_source_health():
    state = store.load_state(store.Path("does-not-exist"))
    store.update_source_health(state, {"a": {"ok": False, "error": "403", "count": 0}}, NOW)
    store.update_source_health(state, {"a": {"ok": False, "error": "403", "count": 0}}, NOW)
    assert state["sources"]["a"]["fail_streak"] == 2
    store.update_source_health(state, {"a": {"ok": True, "count": 3}}, NOW)
    assert state["sources"]["a"]["fail_streak"] == 0
    assert state["sources"]["a"]["last_new_at"] == "2026-10-26T03:17:00Z"
    assert "last_error" not in state["sources"]["a"]
