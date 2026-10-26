import json
from datetime import datetime, timezone

from agent import build_site, store


def test_build_site(tmp_path):
    data, out = tmp_path / "data", tmp_path / "_site"
    now = datetime(2026, 10, 26, 3, 17, tzinfo=timezone.utc)
    store.save_issues(data, [{"id": "c1", "updated_at": "2026-10-26T03:17:00Z", "headline": "반도체",
                              "attempts": 1, "needs_summary": False, "secret": "x",
                              "articles": [{"key": "k", "title": "t", "url": "https://a.kr", "text": "본문"}]}], now)
    store.append_run(data, {"at": "2026-10-26T00:17:00Z", "relevant": 5, "summarized": 2,
                            "ai_model": "claude/claude-sonnet-5-5"}, keep=10)
    store.append_run(data, {"at": "2026-10-26T03:17:00Z", "relevant": 5, "summarized": 3,
                            "ai_model": "openai/gpt-4o-mini", "tokens": {"input": 1}}, keep=10)

    build_site.build(data, out)

    index = (out / "index.html").read_text(encoding="utf-8")
    assert "__BUILD__" not in index  # 캐시 방지용 빌드 시각이 들어감
    issues = json.loads((out / "data" / "issues.json").read_text(encoding="utf-8"))
    issue = issues["issues"][0]
    assert issue["headline"] == "반도체"
    # 게시 허용 목록에 없는 필드는 웹에 나가지 않음
    assert not {"attempts", "needs_summary", "secret"} & set(issue)
    assert set(issue["articles"][0]) == {"title", "url", "source", "lang", "published_at"}
    meta = json.loads((out / "data" / "meta.json").read_text(encoding="utf-8"))
    assert meta["last_run_at"] == "2026-10-26T03:17:00Z"
    # 화면의 AI 이름은 실제로 요약을 만든 모델들 (최근 것부터)
    assert meta["ai"] == "OpenAI gpt-4o-mini, Claude claude-sonnet-5-5"
    # '지금 업데이트' 버튼이 실행을 요청할 저장소
    assert meta["repo"] == "smyz1203/semicon-news-agent"
    index_html = (out / "index.html").read_text(encoding="utf-8")
    assert 'id="refresh"' in index_html and 'id="update-dialog"' in index_html
    assert "tokens" not in meta["runs"][0]  # 웹에는 필요한 필드만
    assert len(meta["sources"]) >= 10 and {"name", "ok"} <= set(meta["sources"][0])
    assert (out / ".nojekyll").exists()


def test_no_token_is_ever_published(tmp_path, monkeypatch):
    # 어떤 환경변수가 있어도 페이지 데이터에 토큰이 들어가지 않아야 함
    monkeypatch.setenv("DEMO_DISPATCH_TOKEN", "github_pat_should_not_appear")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-appear")
    out = build_site.build(tmp_path / "d", tmp_path / "site")
    for path in out.rglob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="ignore")
            assert "should_not_appear" not in text and "should-not-appear" not in text


def test_build_site_without_data(tmp_path):
    out = build_site.build(tmp_path / "empty", tmp_path / "_site")
    meta = json.loads((out / "data" / "meta.json").read_text(encoding="utf-8"))
    assert meta["last_run_at"] is None and meta["runs"] == []
