"""중요도 계산(agent/score.py) 테스트. 설정 파일과 무관하게 작은 기준표로 확인한다."""

import copy

from agent.config import load_config
from agent.score import Scorer

SETTINGS = {
    "companies": {"SK하이닉스": ["SK하이닉스", "하이닉스", "SK hynix"], "퀄컴": ["퀄컴", "Qualcomm"]},
    "importance": {
        "event_types": [
            {"name": "신제품·신기술·양산", "score": 5, "desc": "…"},
            {"name": "수주·공급", "score": 3, "desc": "…"},
            {"name": "칼럼·인터뷰", "score": 1, "desc": "…"},
        ],
        "adjust": {"watch_company": 1, "wide_coverage": 1, "unconfirmed": -1},
        "wide_coverage_sources": 3,
        "watch_companies": ["SK하이닉스", "삼성전자"],  # 삼성전자는 이름 사전에 없음 → 이름 그대로 찾음
    },
}


def issue(event_type="수주·공급", parties=(), sources=("디일렉",), confirmed=True, **extra):
    return {"status": "summarized", "needs_summary": False, "event_type": event_type, "confirmed": confirmed,
            "parties": list(parties), "articles": [{"source": s} for s in sources], **extra}


def test_base_score_only():
    assert Scorer(SETTINGS).score(issue()) == (3, "수주·공급 3점")


def test_watch_company_by_alias():
    score, reason = Scorer(SETTINGS).score(issue(parties=["SK hynix", "퀄컴"]))
    assert score == 4 and reason == "수주·공급 3점, 관심 기업(SK하이닉스) +1 → 4점"
    # 관심 기업이 아닌 당사자는 가산점 없음
    assert Scorer(SETTINGS).score(issue(parties=["퀄컴"]))[0] == 3
    assert Scorer(SETTINGS).score(issue(parties=["삼성전자 파운드리"]))[0] == 4


def test_wide_coverage_counts_distinct_sources():
    assert Scorer(SETTINGS).score(issue(sources=["디일렉", "디일렉", "전자신문"]))[0] == 3
    score, reason = Scorer(SETTINGS).score(issue(sources=["디일렉", "전자신문", "KIPOST"]))
    assert score == 4 and reason == "수주·공급 3점, 매체 3곳 보도 +1 → 4점"


def test_unconfirmed_penalty():
    score, reason = Scorer(SETTINGS).score(issue(confirmed=False))
    assert score == 2 and reason == "수주·공급 3점, 미확정 -1 → 2점"


def test_all_adjustments_in_order():
    score, reason = Scorer(SETTINGS).score(
        issue(parties=["SK하이닉스"], sources=["a", "b", "c"], confirmed=False))
    assert score == 4
    assert reason == "수주·공급 3점, 관심 기업(SK하이닉스) +1, 매체 3곳 보도 +1, 미확정 -1 → 4점"


def test_score_is_kept_within_1_to_5():
    assert Scorer(SETTINGS).score(issue("신제품·신기술·양산", parties=["SK하이닉스"])) == (
        5, "신제품·신기술·양산 5점, 관심 기업(SK하이닉스) +1 → 5점(최고점)")
    assert Scorer(SETTINGS).score(issue("칼럼·인터뷰", confirmed=False)) == (1, "칼럼·인터뷰 1점, 미확정 -1 → 1점(최저점)")


def test_raw_score_keeps_points_cut_by_the_cap():
    # 1~5점으로 맞추며 잘린 가산점·감점은 조정 전 점수에 남음 → 중요도가 같은 이슈끼리 정렬할 때 씀
    capped, plain, floor = issue("신제품·신기술·양산", parties=["SK하이닉스"]), issue("신제품·신기술·양산"),         issue("칼럼·인터뷰", confirmed=False)
    Scorer(SETTINGS).apply([capped, plain, floor])
    assert (capped["importance"], capped["importance_raw"]) == (5, 6)
    assert (plain["importance"], plain["importance_raw"]) == (5, 5)
    assert (floor["importance"], floor["importance_raw"]) == (1, 0)


def test_high_score_type_is_rechecked_once():
    # 기본점이 높은 유형은 '정말 그 유형인지' 확인한 적이 없으면 분류를 다시 함 (확인 기능 전에 분류한 이슈)
    settings = copy.deepcopy(SETTINGS)
    settings["importance"]["strict_min_score"] = 4
    scorer = Scorer(settings)
    assert scorer.needs_classification(issue("신제품·신기술·양산"))
    assert not scorer.needs_classification(issue("신제품·신기술·양산", type_checked=True))
    assert not scorer.needs_classification(issue("수주·공급"))  # 점수가 낮은 유형은 확인하지 않음


def test_adjustment_set_to_zero_is_hidden():
    settings = copy.deepcopy(SETTINGS)
    settings["importance"]["adjust"]["watch_company"] = 0
    assert Scorer(settings).score(issue(parties=["SK하이닉스"])) == (3, "수주·공급 3점")


def test_changing_rubric_rescores_without_ai():
    item = issue()
    Scorer(SETTINGS).apply([item])
    assert item["importance"] == 3
    settings = copy.deepcopy(SETTINGS)
    settings["importance"]["event_types"][1]["score"] = 4
    Scorer(settings).apply([item])
    assert item["importance"] == 4 and item["importance_reason"] == "수주·공급 4점"


def test_unknown_event_type_keeps_old_score_until_reclassified():
    scorer = Scorer(SETTINGS)
    legacy = issue(event_type=None, importance=4, importance_reason="AI가 매긴 예전 근거")
    renamed = issue(event_type="경영진", importance=3, importance_reason="이전 기준표")  # 기준표에서 빠진 유형
    scorer.apply([legacy, renamed])
    assert legacy["importance"] == 4 and legacy["importance_reason"] == "AI가 매긴 예전 근거"
    assert scorer.needs_classification(legacy) and scorer.needs_classification(renamed)
    # 아직 요약 전이거나 다시 요약할 이슈는 요약하면서 분류하므로 따로 분류하지 않음
    assert not scorer.needs_classification(issue(event_type=None, status="pending"))
    assert not scorer.needs_classification(issue(event_type=None, needs_summary=True))
    assert not scorer.needs_classification(issue())


def test_settings_rubric_is_valid():
    # config/settings.yaml을 고치다 실수하면 여기서 잡힘
    imp = load_config().settings["importance"]
    names = [t["name"] for t in imp["event_types"]]
    assert len(names) == len(set(names)), "사건 유형 이름이 중복됨"
    for t in imp["event_types"]:
        assert t["name"].strip() and t["desc"].strip()
        assert isinstance(t["score"], int) and 1 <= t["score"] <= 5, f"{t['name']}의 기본점은 1~5 정수여야 함"
    assert set(imp["adjust"]) == {"watch_company", "wide_coverage", "unconfirmed"}
    assert all(isinstance(v, int) for v in imp["adjust"].values())
    assert isinstance(imp["wide_coverage_sources"], int) and imp["wide_coverage_sources"] >= 2


def test_watch_companies_setting():
    s = load_config().settings
    watch = s["importance"]["watch_companies"]
    assert watch == ["LG디스플레이", "삼성디스플레이", "삼성전자", "SK하이닉스", "엔비디아"]
    # 다른 표기(SK hynix, NVIDIA 등)로도 알아보려면 기업 이름 사전에 있어야 함
    assert all(name in s["companies"] for name in watch), "관심 기업은 기업 이름 사전에 있어야 함"
    # 관련성 판단용 기업은 따로 관리: 관심 기업을 줄여도 TSMC 등 기사는 계속 수집
    relevance = s["keywords"]["companies"]
    assert set(watch) <= set(relevance) and "TSMC" in relevance
    assert all(name in s["companies"] for name in relevance)


def test_new_watch_company_scores_and_old_one_does_not():
    scorer = Scorer(load_config().settings)
    nvidia = {"event_type": "수주·공급", "parties": ["NVIDIA"], "confirmed": True, "articles": [{"source": "A"}]}
    assert scorer.score(nvidia) == (4, "수주·공급 3점, 관심 기업(엔비디아) +1 → 4점")
    tsmc = {**nvidia, "parties": ["TSMC"]}
    assert scorer.score(tsmc) == (3, "수주·공급 3점")  # TSMC는 더 이상 관심 기업이 아님
    both = {**nvidia, "parties": ["엔비디아", "LG디스플레이"]}
    assert scorer.score(both)[1] == "수주·공급 3점, 관심 기업(LG디스플레이, 엔비디아) +1 → 4점"  # 설정 순서대로 표시
