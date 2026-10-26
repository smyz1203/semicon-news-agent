"""수집과 AI를 가짜로 바꿔 끼우고 전체 흐름(main.run)을 실행해 보는 테스트."""

from dataclasses import replace
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
    article("SK hynix begins HBM4 mass production", "https://c.com/1", source="Korea Herald", lang="en"),
    article("갤럭시 버즈 국내 출시", "https://a.kr/2"),
]
RESULTS = {"디일렉": {"ok": True, "count": 3}, "전자신문": {"ok": True, "count": 1}}


class FakeAnalyzer:
    model = "fake-model"

    def __init__(self, settings, stop_on_summary=False):
        self.usage = {"calls": 0, "input": 0, "output": 0, "cache_write": 0, "cache_read": 0}
        self.stop_on_summary = stop_on_summary
        self.summarized = []
        self.classified = []

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
                "tags": ["HBM4"], "companies": ["SK하이닉스"],
                "event_type": TYPES[0], "confirmed": True, "parties": ["SK하이닉스"]}

    def classify(self, issue):
        self.classified.append(issue["id"])
        return {"event_type": TYPES[0], "confirmed": False, "parties": ["삼성전자"]}

    def cost_usd(self):
        return 0.01


from agent.analyze import KEY_ENV as KEYS  # noqa: E402
from agent.config import load_config  # noqa: E402

# 설정의 provider에 맞는 키 이름 (provider를 claude로 바꿔도 테스트가 그대로 동작하도록)
KEY_ENV = KEYS[load_config().settings["provider"]]
TYPES = [t["name"] for t in load_config().settings["importance"]["event_types"]]


def setup(monkeypatch, analyzer_kwargs=None, articles=None):
    monkeypatch.setenv(KEY_ENV, "test")
    feed = ARTICLES if articles is None else articles
    monkeypatch.setattr(main, "collect", lambda cfg, now: (list(feed), {k: dict(v) for k, v in RESULTS.items()}))
    monkeypatch.setattr(main, "Analyzer", lambda s: FakeAnalyzer(s, **(analyzer_kwargs or {})))


def test_full_run(tmp_path, monkeypatch):
    setup(monkeypatch)
    record = main.run(data_dir=tmp_path)

    issues = store.load_issues(tmp_path)
    assert len(issues) == 1
    issue = issues[0]
    assert issue["status"] == "summarized" and issue["event_type"] == TYPES[0]
    # 점수는 AI가 아니라 기준표로 계산: 판단 근거가 사건 유형부터 시작하는 계산식
    assert issue["importance"] == 5 and issue["importance_reason"].startswith(TYPES[0])
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


def test_rebuild_starts_over_with_same_articles(tmp_path, monkeypatch):
    setup(monkeypatch)
    main.run(data_dir=tmp_path)
    assert main.run(data_dir=tmp_path)["new"] == 0  # 같은 기사는 이미 봤으므로 처리할 것 없음
    # 처음부터 다시 만들기: 이슈·이미 본 기사·실행 기록을 비우고 같은 기사를 지금 기준으로 다시 처리
    record = main.run(data_dir=tmp_path, rebuild=True)
    assert record["new"] == 4 and record["new_issues"] == 1
    assert len(store.load_issues(tmp_path)) == 1
    assert store.read_json(tmp_path / "runs.json", []) == [record]
    # --dry-run과 함께 쓰면 아무것도 지우지 않음
    main.run(data_dir=tmp_path, dry_run=True, rebuild=True)
    assert len(store.load_issues(tmp_path)) == 1 and len(store.load_state(tmp_path)["seen"]) == 4


# 키워드 검사 대상인데 관련 단어가 없어 1차 거르기에서 빠지는 기사
OFFTOPIC = replace(article("프로야구 개막전 전석 매진", "https://a.kr/99"), filter=True)


def test_offtopic_articles_are_not_new_next_run(tmp_path, monkeypatch):
    setup(monkeypatch, articles=ARTICLES + [OFFTOPIC])
    record = main.run(data_dir=tmp_path)
    assert record["new"] == 5 and record["relevant"] == 4
    state = store.load_state(tmp_path)
    assert OFFTOPIC.key in state["seen"]

    # 같은 기사로 다시 실행하면 새 기사가 0건이고, 매체의 '마지막 새 기사' 시각도 그대로여야 함
    state["sources"]["디일렉"]["last_new_at"] = "2000-01-01T00:00:00Z"
    store.save_state(tmp_path, state)
    record2 = main.run(data_dir=tmp_path)
    assert record2["new"] == 0
    assert store.load_state(tmp_path)["sources"]["디일렉"]["last_new_at"] == "2000-01-01T00:00:00Z"


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
    setup(monkeypatch, articles=feed + [OFFTOPIC])
    records = [main.run(data_dir=tmp_path, limit=1) for _ in range(3)]
    assert [r["deferred"] for r in records] == [2, 1, 0]
    # 미룬 관련 기사는 다음 실행에서 다시 새 기사로 잡히고, 관련 없는 기사는 첫 실행에만 셈
    assert [r["new"] for r in records] == [4, 2, 1]
    # '관련 기사'는 처리한 기사만 셈: 미룬 기사를 실행마다 다시 세면 수집 현황 합계가 부풀려짐
    assert [r["relevant"] for r in records] == [1, 1, 1]
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
    record = main.run(data_dir=tmp_path)
    [issue] = store.load_issues(tmp_path)
    assert issue["needs_summary"] is False
    # 새 이슈는 없지만 요약은 1건: 이미 요약한 이슈를 다시 요약한 것
    assert record["new_issues"] == 0 and record["summarized"] == 1 and record["resummarized"] == 1


def test_given_up_issue_is_not_counted_as_waiting(tmp_path, monkeypatch):
    from agent.analyze import AIError

    class Broken(FakeAnalyzer):
        def summarize(self, issue, texts):
            raise AIError("요약이 기사 문장을 그대로 옮김")

    setup(monkeypatch, articles=ARTICLES[:1])
    monkeypatch.setattr(main, "Analyzer", lambda s: Broken(s))
    records = [main.run(data_dir=tmp_path) for _ in range(3)]
    # 3번 실패하면 더 시도하지 않으므로 '대기'가 아니라 '요약 실패'로 셈
    assert [(r["pending"], r["failed"]) for r in records] == [(1, 0), (1, 0), (0, 1)]
    assert records[0]["new_issues"] == 1 and records[0]["summarized"] == 0


def test_without_api_key_articles_wait_for_next_run(tmp_path, monkeypatch):
    setup(monkeypatch)
    monkeypatch.delenv(KEY_ENV)
    record = main.run(data_dir=tmp_path)
    # AI 없이 이슈를 만들면 중복이 남으므로, 아무 이슈도 만들지 않고 기사도 '본 것'으로 기록하지 않음
    assert store.load_issues(tmp_path) == []
    assert store.load_state(tmp_path)["seen"] == {}
    assert KEY_ENV in record["ai_stopped"] and record["deferred"] == 3

    # 키가 생기면 미뤘던 기사가 정상적으로 묶여 처리됨
    setup(monkeypatch)
    main.run(data_dir=tmp_path)
    [issue] = store.load_issues(tmp_path)
    assert issue["status"] == "summarized" and len(issue["articles"]) == 3


def test_invalid_provider_stops_with_clear_message(tmp_path, monkeypatch):
    import pytest
    real = load_config()
    real.settings["provider"] = "gemini"
    monkeypatch.setattr(main, "load_config", lambda: real)
    with pytest.raises(SystemExit, match="provider"):
        main.run(data_dir=tmp_path)


def test_legacy_issue_is_reclassified_then_scored_by_rubric(tmp_path, monkeypatch):
    # AI가 직접 점수를 매기던 예전 이슈: 사건 유형이 없음
    legacy = store.new_issue({}, NOW)
    legacy.update(headline="삼성전자 HBM 가격 인상 검토", summary=["가.", "나.", "다."], status="summarized",
                  needs_summary=False, importance=4, importance_reason="대형 수주·공급 계약에 해당")
    legacy["articles"] = [{"key": "k1", "title": "삼성 HBM 가격 인상", "url": "https://a.kr/9", "source": "디일렉",
                           "lang": "ko", "published_at": store.to_iso(NOW)}]
    store.save_issues(tmp_path, [legacy], NOW)

    # AI를 쓸 수 없으면 분류하지 못하므로 예전 점수를 그대로 둠
    setup(monkeypatch, articles=[])
    monkeypatch.delenv(KEY_ENV)
    main.run(data_dir=tmp_path)
    [issue] = store.load_issues(tmp_path)
    assert issue["importance"] == 4 and issue["importance_reason"] == "대형 수주·공급 계약에 해당"

    # AI가 있으면 요약은 그대로 두고 분류만 다시 해서 기준표로 점수를 계산
    setup(monkeypatch, articles=[])
    record = main.run(data_dir=tmp_path)
    [issue] = store.load_issues(tmp_path)
    assert record["classified"] == 1 and record["summarized"] == 0
    assert issue["summary"] == ["가.", "나.", "다."]
    assert issue["event_type"] == TYPES[0] and issue["confirmed"] is False
    assert issue["importance_reason"].startswith(TYPES[0]) and "미확정" in issue["importance_reason"]

    # 한 번 분류한 이슈는 다시 묻지 않음
    record = main.run(data_dir=tmp_path)
    assert record["classified"] == 0


def test_cluster_failure_defers_instead_of_creating_unmerged_issues(tmp_path, monkeypatch):
    from agent.analyze import AIError

    class Failing(FakeAnalyzer):
        def cluster(self, groups, active):
            raise AIError("응답 형식 오류")

    setup(monkeypatch)
    monkeypatch.setattr(main, "Analyzer", lambda s: Failing(s))
    record = main.run(data_dir=tmp_path)
    assert store.load_issues(tmp_path) == [] and record["deferred"] == 3
