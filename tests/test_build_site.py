import json
from datetime import datetime, timezone

from agent import build_site, store


def test_build_site(tmp_path):
    data, out = tmp_path / "data", tmp_path / "_site"
    now = datetime(2026, 10, 26, 3, 17, tzinfo=timezone.utc)
    store.save_issues(data, [{"id": "c1", "updated_at": "2026-10-26T03:17:00Z", "headline": "반도체"}], now)
    store.append_run(data, {"at": "2026-10-26T03:17:00Z", "relevant": 5, "tokens": {"input": 1}}, keep=10)

    build_site.build(data, out)

    index = (out / "index.html").read_text(encoding="utf-8")
    assert "__BUILD__" not in index  # 캐시 방지용 빌드 시각이 들어감
    issues = json.loads((out / "data" / "issues.json").read_text(encoding="utf-8"))
    assert issues["issues"][0]["headline"] == "반도체"
    meta = json.loads((out / "data" / "meta.json").read_text(encoding="utf-8"))
    assert meta["last_run_at"] == "2026-10-26T03:17:00Z"
    assert "tokens" not in meta["runs"][0]  # 웹에는 필요한 필드만
    assert len(meta["sources"]) >= 10 and {"name", "ok"} <= set(meta["sources"][0])
    assert (out / ".nojekyll").exists()


def test_build_site_without_data(tmp_path):
    out = build_site.build(tmp_path / "empty", tmp_path / "_site")
    meta = json.loads((out / "data" / "meta.json").read_text(encoding="utf-8"))
    assert meta["last_run_at"] is None and meta["runs"] == []
