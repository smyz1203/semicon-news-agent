import json
from datetime import datetime, timezone

from agent import build_site, store
from agent.config import Source


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
    assert "importance_raw" in issue  # 웹이 같은 중요도끼리 정렬할 때 씀
    assert set(issue["articles"][0]) == {"title", "url", "source", "lang", "published_at"}
    meta = json.loads((out / "data" / "meta.json").read_text(encoding="utf-8"))
    assert meta["last_run_at"] == "2026-10-26T03:17:00Z"
    # 화면의 AI 이름은 실제로 요약을 만든 모델들 (최근 것부터)
    assert meta["ai"] == "OpenAI gpt-4o-mini, Claude claude-sonnet-5-5"
    # '지금 업데이트' 버튼이 실행을 요청할 저장소
    assert meta["repo"] == "smyz1203/semicon-news-agent"
    index_html = (out / "index.html").read_text(encoding="utf-8")
    assert 'id="refresh"' in index_html and 'id="update-dialog"' in index_html
    # 브리핑 맨 아래 중요도 기준표: settings.yaml의 기준표 그대로
    rubric = meta["rubric"]
    assert len(rubric["event_types"]) == 17 and {"name", "score", "desc"} == set(rubric["event_types"][0])
    assert [a["value"] for a in rubric["adjust"]] == [1, 1, -1]
    assert rubric["watch_companies"] == ["LG디스플레이", "삼성디스플레이", "삼성전자", "SK하이닉스", "엔비디아"]
    assert 'id="rubric"' in index_html and "가 판단했으며" in index_html
    assert "tokens" not in meta["runs"][0]  # 웹에는 필요한 필드만
    assert len(meta["sources"]) >= 10 and {"name", "ok"} <= set(meta["sources"][0])
    names = [s["name"] for s in meta["sources"]]
    assert len(names) == len(set(names))  # 같은 언론사의 분야별 RSS는 수집 현황에서 한 줄
    assert (out / ".nojekyll").exists()


def test_outlet_status_merges_section_feeds():
    # 수집은 RSS마다 하지만 화면에는 매체 하나로. 이름만 비슷한 다른 매체는 합치지 않음
    feeds = [Source("dt-ict", "디지털타임스", "u1", "ko", True, section="IT"),
             Source("dt-industry", "디지털타임스", "u2", "ko", True, section="산업"),
             Source("semieng", "Semiconductor Engineering", "u3", "en", False),
             Source("semidigest", "Semiconductor Digest", "u4", "en", False)]
    health = {"dt-ict": {"fail_streak": 0, "last_new_at": "2026-10-26T01:00:00Z"},
              "dt-industry": {"fail_streak": 2, "last_new_at": "2026-10-26T03:00:00Z"},
              "semieng": {"fail_streak": 0}}
    rows = {r["name"]: r for r in build_site._outlet_status(feeds, health)}
    assert list(rows) == ["디지털타임스", "Semiconductor Engineering", "Semiconductor Digest"]
    dt = rows["디지털타임스"]
    assert dt["sections"] == ["IT", "산업"] and (dt["rss"], dt["failed_rss"]) == (2, 1)
    assert dt["ok"] is False and dt["fail_streak"] == 2  # 분야 RSS 하나라도 실패하면 실패로 표시
    assert dt["last_new_at"] == "2026-10-26T03:00:00Z"  # 두 RSS 중 가장 최근 새 기사
    assert rows["Semiconductor Engineering"]["ok"] is True
    assert rows["Semiconductor Digest"]["ok"] is None  # 아직 수집 전


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
