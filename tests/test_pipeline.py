"""수집과 AI를 가짜로 바꿔 끼우고 전체 흐름(main.run)을 실행해 보는 테스트."""

from datetime import datetime, timedelta, timezone

from agent import main, store
from agent.analyze import AIStopped
from agent.collect import Article, url_key

NOW = datetime.now(timezone.utc)


def article(title, url, source="디일렉", lang="ko", text="본문"):
    return Article(key=url_key(url), url=url, title=title, source=source, source_id=source,
                   lang=lang, published_at=NOW - timedelta(hours=1), text=text, filter=False)


ARTICLES = [
    article("SK하이닉스, HBM4 세계 최초 양산 돌입", "https://a.kr/1"),
    article("[속보] SK하이닉스 HBM4 세계 최초 양산 돌입", "https://b.kr/1", source="전자신문"),  # 1차에서 묶임
    article("SK hynix begins HBM4 mass production", "https://c.com/1", source="The Elec", lang="en"),
    article("갤럭시 버즈 국내 출시", "https://a.kr/2"),
]
RESULTS = {"디일렉": {"ok": True, "count": 3}, "전자신문": {"ok": True, "count": 1}}


class FakeAnalyzer:
    def __init__(self, settings, stop_on_summary=False):
        self.usage = {"calls": 0, "input": 0, "output": 0, "cache_write": 0, "cache_read": 0}
        self.stop_on_summary = stop_on_summary
        self.summarized = []

    def cluster(self, groups, active):
        # 국문·영문 HBM4 기사를 한 이슈로 (이미 있으면 그 이슈에), 버즈 기사는 관련 없음, 나머지는 각자 새 이슈
        hbm = next((i["id"] for i in active if "HBM4" in i["headline"]), "new:hbm4")
        out = {}
        for i, g in enumerate(groups):
            if "버즈" in g.lead.title:
                out[i] = None
            elif "HBM4" in g.lead.title:
                out[i] = hbm
            else:
                out[i] = f"new:{i}"
        return out

    def summarize(self, issue, texts):
        if self.stop_on_summary:
            raise AIStopped("크레딧 부족")
        self.summarized.append(issue["id"])
        return {"headline": "SK하이닉스 HBM4 양산", "summary": ["a", "b", "c"], "category": "메모리",
                "tags": ["HBM4"], "companies": ["SK하이닉스"], "importance": 5, "importance_reason": "양산"}

    def cost_usd(self):
        return 0.01


def setup(monkeypatch, analyzer_kwargs=None, articles=None):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    feed = ARTICLES if articles is None else articles
    monkeypatch.setattr(main, "collect", lambda cfg, now: (list(feed), {k: dict(v) for k, v in RESULTS.items()}))
    monkeypatch.setattr(main, "Analyzer", lambda s: FakeAnalyzer(s, **(analyzer_kwargs or {})))


def test_full_run(tmp_path, monkeypatch):
    setup(monkeypatch)
    record = main.run(data_dir=tmp_path)

    issues = store.load_issues(tmp_path)
    assert len(issues) == 1
    issue = issues[0]
    assert issue["status"] == "summarized" and issue["importance"] == 5
    assert issue["region"] == "both"
    assert len(issue["articles"]) == 3
    assert "text" not in issue["articles"][0]  # 본문은 저장하지 않음
    assert record["new_issues"] == 1 and record["irrelevant_by_ai"] == 1
    state = store.load_state(tmp_path)
    assert state["last_success_at"] is not None
    assert len(state["seen"]) == 4  # 관련 없는 기사도 다시 묻지 않도록 기록

    # 같은 기사로 다시 실행하면 새로 처리할 것이 없어야 함
    record2 = main.run(data_dir=tmp_path)
    assert record2["new"] == 0 and record2["new_issues"] == 0
    assert len(store.load_issues(tmp_path)) == 1


def test_ai_stopped_publishes_pending_then_retries(tmp_path, monkeypatch):
    setup(monkeypatch, {"stop_on_summary": True})
    record = main.run(data_dir=tmp_path)
    [issue] = store.load_issues(tmp_path)
    assert issue["status"] == "pending"
    assert record["ai_stopped"] and any("AI 중단" in a for a in record["alerts"])

    # 다음 실행에서 AI가 되살아나면 요약 대기 이슈를 요약함
    setup(monkeypatch)
    main.run(data_dir=tmp_path)
    [issue] = store.load_issues(tmp_path)
    assert issue["status"] == "summarized"


def test_deferred_groups_are_processed_next_run(tmp_path, monkeypatch):
    # 서로 다른 기사 3건, 실행당 상한 1 → 세 번의 실행에 걸쳐 모두 처리돼야 함 (잃어버리면 안 됨)
    titles = ["LG디스플레이 8.6세대 OLED 투자 확정", "TSMC 2나노 미국 공장 양산 시작", "마이크론 일본 히로시마 HBM 증설"]
    feed = [article(t, f"https://d.kr/{i}") for i, t in enumerate(titles)]
    setup(monkeypatch, articles=feed)
    records = [main.run(data_dir=tmp_path, limit=1) for _ in range(3)]
    assert [r["deferred"] for r in records] == [2, 1, 0]
    assert len(store.load_issues(tmp_path)) == 3


def test_failed_resummary_is_retried(tmp_path, monkeypatch):
    setup(monkeypatch, articles=ARTICLES[:1])
    main.run(data_dir=tmp_path)
    # 다른 매체 기사가 붙었는데 그 실행에서 AI가 멈춤 → 다음 실행에서 다시 요약해야 함
    setup(monkeypatch, {"stop_on_summary": True}, articles=ARTICLES[:3])
    main.run(data_dir=tmp_path)
    [issue] = store.load_issues(tmp_path)
    assert issue["needs_summary"] is True and issue["status"] == "summarized"
    setup(monkeypatch, articles=ARTICLES[:3])
    main.run(data_dir=tmp_path)
    [issue] = store.load_issues(tmp_path)
    assert issue["needs_summary"] is False


def test_without_api_key_everything_is_pending(tmp_path, monkeypatch):
    setup(monkeypatch)
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    record = main.run(data_dir=tmp_path)
    issues = store.load_issues(tmp_path)
    # AI가 없으면 관련성 판단도 못 하므로, 1차 거르기를 통과한 묶음마다 요약 대기 이슈가 됨
    assert issues and all(i["status"] == "pending" for i in issues)
    assert "ANTHROPIC_API_KEY" in record["ai_stopped"]
