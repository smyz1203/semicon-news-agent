from datetime import datetime, timezone
from types import SimpleNamespace

import anthropic
import httpx2
import openai
import pytest

from agent.analyze import (AIError, AIStopped, Analyzer, clean_facts, clean_summary, copies_source,
                           resolve_clusters)
from agent.collect import Article
from agent.config import load_config
from agent.dedupe import Group
from agent.models import (ArticleFacts, IssueFacts, IssueSummary, SameEvent, SameEventResult, TriageResult,
                          TypeCheck)

SETTINGS = load_config().settings
OPENAI = {**SETTINGS, "provider": "openai"}
CLAUDE = {**SETTINGS, "provider": "claude"}
TYPES = [t["name"] for t in SETTINGS["importance"]["event_types"]]
STRICT = SETTINGS["importance"]["strict_min_score"]
HIGH = [t["name"] for t in SETTINGS["importance"]["event_types"] if t["score"] >= STRICT]  # 확실할 때만 주는 유형
LOW = [t["name"] for t in SETTINGS["importance"]["event_types"] if t["score"] < STRICT]


class FakeCalls:
    """SDK의 parse 자리에 들어가는 가짜. 미리 정한 응답이나 예외를 차례로 돌려준다."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


class FakeRaw:
    """with_raw_response.parse()가 돌려주는 원시 응답 흉내: HTTP 본문(usage 포함) + parse()."""

    def __init__(self, usage: dict, parsed=None, parse_error: Exception | None = None):
        self.http_response = SimpleNamespace(json=lambda: {"usage": usage})
        self._parsed, self._error = parsed, parse_error

    def parse(self):
        if self._error:
            raise self._error
        return self._parsed


def openai_client(*replies):
    calls = FakeCalls(replies)
    return SimpleNamespace(responses=SimpleNamespace(with_raw_response=calls)), calls


def claude_client(*replies):
    calls = FakeCalls(replies)
    return SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(with_raw_response=calls))), calls


OPENAI_USAGE = {"input_tokens": 1000, "output_tokens": 200, "input_tokens_details": {"cached_tokens": 400}}
CLAUDE_USAGE = {"input_tokens": 1000, "output_tokens": 200, "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 500}


def openai_ok(parsed, status="completed", refusal=False, parse_error=None):
    part = SimpleNamespace(type="refusal" if refusal else "output_text")
    resp = SimpleNamespace(status=status, incomplete_details=SimpleNamespace(reason="max_output_tokens"),
                           output=[SimpleNamespace(type="message", content=[part])], output_parsed=parsed)
    return FakeRaw(OPENAI_USAGE, resp, parse_error)


def claude_ok(parsed, stop_reason="end_turn", parse_error=None):
    return FakeRaw(CLAUDE_USAGE, SimpleNamespace(parsed_output=parsed, stop_reason=stop_reason), parse_error)


def status_error(module, cls, status, message, body=None):
    response = httpx2.Response(status, request=httpx2.Request("POST", "https://api.example.com/v1"))
    return getattr(module, cls)(message, response=response, body=body)


SUMMARY = IssueSummary(headline="SK하이닉스 HBM4 양산", summary=["첫째.", "둘째.", "셋째.", "넷째."],
                       category="메모리", tags=["HBM4", "hbm4", " AI "], companies=["SK하이닉스", "엔비디아"],
                       event_type=LOW[0], confirmed=True, parties=["SK하이닉스", " sk하이닉스 "])
FACTS = IssueFacts(event_type=TYPES[-1], confirmed=False, parties=["삼성전자"])
ISSUE = {"id": "c1", "headline": "", "companies": [], "articles": [
    {"key": "k1", "title": "제목1", "source": "디일렉", "lang": "ko", "published_at": "2026-10-26T00:00:00Z"},
]}
SUMMARIZED = {**ISSUE, "headline": "삼성전자 HBM4 양산 검토", "summary": ["가.", "나.", "다."], "status": "summarized"}


# ── 응답 검증 (제공사와 무관) ────────────────────────────────


def test_resolve_clusters_merges_only_confirmed_pairs():
    same = [("a0", "a2", 0.5),                       # 새 기사끼리 같은 사건 → 같은 새 이슈
            ("a1", "c-old", 0.3),                    # 기존 이슈와 같은 사건 → 그 이슈로
            ("a3", "c-x", 0.2), ("a3", "c-y", 0.6)]  # 기존 이슈 둘과 같다고 나오면 더 비슷한 쪽 하나에만
    out = resolve_clusters(6, {0, 1, 2, 3, 5}, same)
    assert out[0] == out[2] and out[0].startswith("new:")
    assert out[1] == "c-old" and out[3] == "c-y"
    assert out[4] is None                            # 관련 없음
    assert out[5] == "new:a5"                        # 같은 사건으로 확인된 짝이 없으면 혼자 새 이슈


def test_clean_summary():
    out = clean_summary(SUMMARY, SETTINGS["categories"], TYPES)
    assert out["summary"] == ["첫째.", "둘째.", "셋째."]
    assert out["tags"] == ["HBM4", "AI"]
    assert out["event_type"] == LOW[0] and out["confirmed"] is True
    assert out["parties"] == ["SK하이닉스"]
    assert "importance" not in out  # 점수는 AI가 아니라 agent/score.py가 계산
    bad = SUMMARY.model_copy(update={"category": "스포츠"})
    assert clean_summary(bad, SETTINGS["categories"], TYPES)["category"] == "기타"
    with pytest.raises(AIError):
        clean_summary(SUMMARY.model_copy(update={"summary": [" "]}), SETTINGS["categories"], TYPES)


def test_event_type_outside_rubric_is_rejected():
    with pytest.raises(AIError):
        clean_summary(SUMMARY.model_copy(update={"event_type": "기타"}), SETTINGS["categories"], TYPES)
    with pytest.raises(AIError):
        clean_facts(FACTS.model_copy(update={"event_type": "기타"}), TYPES)


def test_clean_summary_enforces_length_limits():
    long = SUMMARY.model_copy(update={"headline": "가" * 200, "summary": ["나" * 500], "tags": ["다" * 99],
                                      "parties": ["라" * 99]})
    out = clean_summary(long, SETTINGS["categories"], TYPES)
    assert len(out["headline"]) <= 60 and out["headline"].endswith("…")
    assert len(out["summary"][0]) <= 120
    assert len(out["tags"][0]) <= 30
    assert len(out["parties"][0]) <= 30


def test_copies_source():
    body = "SK하이닉스는 26일 경기 이천 본사에서 HBM4 12단 제품의 양산 출하를 시작했다고 밝혔다. 회사는 내년 생산량을 두 배로 늘린다."
    copied = "SK하이닉스는 26일 경기 이천 본사에서 HBM4 12단 제품의 양산 출하를 시작했다고 밝혔다."
    rewritten = "SK하이닉스가 HBM4 12단 양산 출하에 들어갔다."
    assert copies_source([copied], [body])
    assert not copies_source([rewritten], [body])
    assert not copies_source([copied], [""])


def test_unknown_provider_is_rejected():
    with pytest.raises(ValueError):
        Analyzer({**SETTINGS, "provider": "gemini"}, client=object())


# ── OpenAI ──────────────────────────────────────────────────


def test_openai_request_shape_and_usage():
    client, calls = openai_client(openai_ok(SUMMARY))
    analyzer = Analyzer(OPENAI, client=client)
    out = analyzer.summarize(ISSUE, {"k1": "본문" * 2000})
    assert out["headline"] == "SK하이닉스 HBM4 양산"
    call = calls.calls[0]
    assert call["model"] == SETTINGS["openai"]["model"]
    # 사건 유형은 기준표의 이름 중 하나로만 답하도록 응답 형식(enum)에서 강제
    assert issubclass(call["text_format"], IssueSummary)
    assert call["text_format"].model_json_schema()["properties"]["event_type"]["enum"] == TYPES
    assert call["store"] is False
    # 지시문에는 사건 유형과 설명만 있고, AI가 점수를 매기라는 내용은 없음
    first = SETTINGS["importance"]["event_types"][0]
    assert f"{first['name']}: {first['desc']}" in call["instructions"]
    assert "importance" not in call["instructions"]
    if SETTINGS["openai"]["reasoning"]:
        assert call["reasoning"] == {"effort": SETTINGS["effort"]["summarize"]}
    assert "temperature" not in call
    # 본문은 설정한 글자 수까지만 보냄
    assert "본문" * (SETTINGS["limits"]["summary_input_chars"] // 2) in call["input"]
    assert "본문" * 1000 not in call["input"]
    # 캐시된 입력 토큰은 따로 집계
    assert analyzer.usage == {"calls": 1, "input": 600, "output": 200, "cache_write": 0, "cache_read": 400}
    assert analyzer.cost_usd() > 0


def group(title, source="전자신문"):
    art = Article(key=title, url=f"https://e.com/{title}", title=title, source=source, source_id=source, lang="ko",
                  published_at=datetime(2026, 10, 26, tzinfo=timezone.utc), text="", filter=True)
    return Group(articles=[art], norm="", companies=frozenset())


def test_cluster_merges_only_pairs_ai_confirms():
    # 2026-10-26 실제 사례: 같은 뉴스(DB하이텍 품질 인증)가 두 이슈로 나뉘고, 다른 사건(솔리다임 IPO ← 글래스 기판)이 묶였음
    issues = [{"id": "c-ipo", "headline": "SK하이닉스, 솔리다임 IPO 절차 중단", "summary": ["SK하이닉스가 상장을 멈췄다."],
               "articles": [{"title": "SK hynix Halts Solidigm IPO Plans"}]}]
    groups = [group(t) for t in ["DB하이텍, 자동차 품질 인증 갱신…車 반도체 경쟁력 강화",
                                 "DB하이텍, 파운드리 분야 자동차 품질 표준 인증 갱신",
                                 "[안테나] 글래스 기판 생산 현장에서 중요도가 높아지는 기술",
                                 "中企수출 역대 3분기 최고… K-뷰티 쌍끌이"]]
    triage = TriageResult(articles=[
        ArticleFacts(article_id="a0", event="DB하이텍, 자동차 반도체 품질 인증 갱신", relevant=True),
        ArticleFacts(article_id="a1", event="DB하이텍, 파운드리 자동차 품질 표준 인증 갱신", relevant=True),
        ArticleFacts(article_id="a2", event="글래스 기판 생산 기술의 중요성 커짐", relevant=True),
        ArticleFacts(article_id="a3", event="중소기업 3분기 수출 역대 최고", relevant=False),
    ])
    yes, no = (SameEventResult(pairs=[SameEvent(pair_id="p0", reason="비교", same=v)]) for v in (True, False))
    # 같은 질문을 세 번 묻고 다수결: 셋 중 둘이 같다고 하면 묶음
    client, calls = openai_client(*[openai_ok(triage)] * 3, openai_ok(yes), openai_ok(no), openai_ok(yes))
    out = Analyzer(OPENAI, client=client).cluster(groups, issues)
    assert out[0] == out[1] and out[0].startswith("new:")   # 같은 뉴스는 한 이슈로
    assert out[2].startswith("new:") and out[2] != out[0]   # 다른 사건은 기존 이슈에 붙지 않음
    assert out[3] is None
    # ① 관련성 기준은 중요도 기준표의 사건 유형과 같음
    assert calls.calls[0]["text_format"] is TriageResult
    assert all(t in calls.calls[0]["instructions"] for t in TYPES)
    # ③ 규칙으로 고른 후보 짝만 확인을 부탁함 (글래스 기판 기사는 비슷한 이슈가 없어 묻지 않음)
    assert len(calls.calls) == 6 and calls.calls[3]["text_format"] is SameEventResult
    assert "파운드리 분야 자동차 품질" in calls.calls[3]["input"] and "글래스 기판" not in calls.calls[3]["input"]
    assert list(SameEvent.model_fields) == ["pair_id", "reason", "same"]  # 비교한 뒤에 판단하도록 순서를 정함


def test_cluster_does_not_merge_when_ai_is_unsure():
    groups = [group("DB하이텍, 자동차 품질 인증 갱신"), group("DB하이텍, 파운드리 자동차 품질 인증 갱신")]
    triage = TriageResult(articles=[ArticleFacts(article_id=f"a{i}", event=g.lead.title, relevant=True)
                                    for i, g in enumerate(groups)])
    yes, no = (SameEventResult(pairs=[SameEvent(pair_id="p0", reason="애매함", same=v)]) for v in (True, False))
    client, _ = openai_client(*[openai_ok(triage)] * 3, openai_ok(yes), openai_ok(no), openai_ok(no))
    assert Analyzer(OPENAI, client=client).cluster(groups, []) == {0: "new:a0", 1: "new:a1"}  # 셋 중 하나만 같다고 함


def test_cluster_triages_in_batches_and_keeps_articles_ai_missed():
    titles = [f"관련 없는 기사 {i}" for i in range(20)] + ["가나", "다라", "마바", "사아", "자차"]
    no, yes = (TriageResult(articles=[ArticleFacts(article_id=f"a{i}", event="", relevant=v) for i in range(20)])
               for v in (False, True))
    missed = TriageResult(articles=[])  # 뒤의 5건은 AI가 세 번 다 빠뜨림
    client, calls = openai_client(openai_ok(no), openai_ok(yes), openai_ok(no), *[openai_ok(missed)] * 3)
    out = Analyzer(OPENAI, client=client).cluster([group(t) for t in titles], [])
    assert len(calls.calls) == 6  # 20건씩 나눠, 묶음마다 세 번 물어봄
    # 앞의 20건: 셋 중 둘이 관련 없다고 함 → 관련 없음
    assert all(out[i] is None for i in range(20))
    assert [out[i] for i in range(20, 25)] == [f"new:a{i}" for i in range(20, 25)]  # 빠뜨린 기사는 혼자 새 이슈로 남김


def test_openai_without_reasoning_omits_effort():
    settings = {**OPENAI, "openai": {**SETTINGS["openai"], "reasoning": False}}
    client, calls = openai_client(openai_ok(SUMMARY))
    Analyzer(settings, client=client).summarize(ISSUE, {})
    assert "reasoning" not in calls.calls[0]


@pytest.mark.parametrize("reply", [
    openai_ok(None, refusal=True),
    openai_ok(SUMMARY, status="incomplete"),
    openai_ok(None),
    openai_ok(None, parse_error=ValueError("잘린 JSON")),  # 출력이 잘리면 SDK가 해석 중 ValidationError
])
def test_openai_bad_responses_are_single_failures_but_still_billed(reply):
    client, _ = openai_client(reply)
    analyzer = Analyzer(OPENAI, client=client)
    with pytest.raises(AIError):
        analyzer.summarize(ISSUE, {})
    # 쓸 수 없는 응답도 요금은 나가므로 토큰이 집계돼야 함
    assert analyzer.usage["output"] == 200 and analyzer.cost_usd() > 0


@pytest.mark.parametrize("body", [{"code": "insufficient_quota", "type": "insufficient_quota"},
                                  {"code": "credit_balance_exhausted", "type": "insufficient_quota"},
                                  {"code": "project_spend_limit_exceeded", "type": "requests"}])
def test_openai_out_of_credits_stops_ai(body):
    err = status_error(openai, "RateLimitError", 429, "You exceeded your current quota", body=body)
    client, _ = openai_client(err)
    with pytest.raises(AIStopped):
        Analyzer(OPENAI, client=client).summarize(ISSUE, {})


def test_openai_normal_rate_limit_is_single_failure():
    err = status_error(openai, "RateLimitError", 429, "Rate limit reached", body={"code": "rate_limit_exceeded"})
    client, _ = openai_client(err)
    with pytest.raises(AIError):
        Analyzer(OPENAI, client=client).summarize(ISSUE, {})


def test_openai_auth_error_stops_ai():
    client, _ = openai_client(status_error(openai, "AuthenticationError", 401, "Incorrect API key"))
    with pytest.raises(AIStopped):
        Analyzer(OPENAI, client=client).summarize(ISSUE, {})


def test_openai_three_failures_in_a_row_stop_ai():
    err = status_error(openai, "InternalServerError", 500, "server error")
    client, _ = openai_client(err, err, err)
    analyzer = Analyzer(OPENAI, client=client)
    for _ in range(2):
        with pytest.raises(AIError):
            analyzer.summarize(ISSUE, {})
    with pytest.raises(AIStopped):
        analyzer.summarize(ISSUE, {})


def test_summary_that_copies_article_is_rejected():
    body = "가나다라마바사아자차카타파하" * 5
    copied = SUMMARY.model_copy(update={"summary": [body[:60], "둘째.", "셋째."]})
    client, _ = openai_client(openai_ok(copied))
    with pytest.raises(AIError):
        Analyzer(OPENAI, client=client).summarize(ISSUE, {"k1": body})


def test_high_score_type_is_kept_only_when_confirmed():
    yes, no = TypeCheck(reason="새 제품 출시", fits=True), TypeCheck(reason="애매함", fits=False)
    client, calls = openai_client(openai_ok(SUMMARY.model_copy(update={"event_type": HIGH[0]})),
                                  openai_ok(yes), openai_ok(no), openai_ok(yes))  # 셋 중 둘이 맞다고 함
    out = Analyzer(OPENAI, client=client).summarize(ISSUE, {})
    assert out["event_type"] == HIGH[0] and out["type_checked"] is True and len(calls.calls) == 4
    assert calls.calls[1]["text_format"] is TypeCheck and HIGH[0] in calls.calls[1]["input"]


def test_unconfirmed_high_score_type_is_reclassified_without_it():
    # 실제 사례: 'ASM, 한국 반도체 학생에게 장학금 수여'가 신제품·신기술·양산(5점)으로 분류됨
    no = TypeCheck(reason="장학금 수여는 신제품이 아님", fits=False)
    award = IssueFacts(event_type=LOW[-1], confirmed=True, parties=["ASM"])
    client, calls = openai_client(openai_ok(SUMMARY.model_copy(update={"event_type": HIGH[0]})),
                                  *[openai_ok(no)] * 3, openai_ok(award))
    out = Analyzer(OPENAI, client=client).summarize(ISSUE, {})
    assert out["event_type"] == LOW[-1] and out["headline"] == "SK하이닉스 HBM4 양산"  # 요약은 그대로, 유형만 다시
    retry = calls.calls[4]  # 아니라고 확인된 유형은 고를 수 없게 빼고 다시 물음
    assert HIGH[0] not in retry["text_format"].model_json_schema()["properties"]["event_type"]["enum"]
    assert HIGH[0] in retry["input"]


def test_reclassified_high_type_is_checked_again():
    no = TypeCheck(reason="", fits=False)
    second = IssueFacts(event_type=HIGH[1], confirmed=True, parties=[])
    final = IssueFacts(event_type=LOW[0], confirmed=True, parties=[])
    client, calls = openai_client(openai_ok(SUMMARY.model_copy(update={"event_type": HIGH[0]})),
                                  *[openai_ok(no)] * 3, openai_ok(second), *[openai_ok(no)] * 3, openai_ok(final))
    out = Analyzer(OPENAI, client=client).summarize(ISSUE, {})
    assert out["event_type"] == LOW[0] and len(calls.calls) == 9


def test_low_score_type_is_not_checked():
    client, calls = openai_client(openai_ok(SUMMARY))
    assert Analyzer(OPENAI, client=client).summarize(ISSUE, {})["type_checked"] is True
    assert len(calls.calls) == 1  # 점수가 낮은 유형은 확인하지 않음


def test_classify_sends_existing_summary_and_returns_facts():
    client, calls = openai_client(openai_ok(FACTS))
    out = Analyzer(OPENAI, client=client).classify(SUMMARIZED)
    assert out == {"event_type": TYPES[-1], "confirmed": False, "parties": ["삼성전자"], "type_checked": True}
    call = calls.calls[0]
    assert issubclass(call["text_format"], IssueFacts)
    assert call["text_format"].model_json_schema()["properties"]["event_type"]["enum"] == TYPES
    # 기사 본문은 저장돼 있지 않으므로 헤드라인·요약·기사 제목으로 분류
    assert "삼성전자 HBM4 양산 검토" in call["input"] and "제목1" in call["input"]


# ── Claude (설정에서 provider: claude로 되돌렸을 때) ─────────────


def test_claude_request_shape_and_usage():
    client, calls = claude_client(claude_ok(SUMMARY))
    analyzer = Analyzer(CLAUDE, client=client)
    out = analyzer.summarize(ISSUE, {"k1": "본문"})
    assert out["headline"] == "SK하이닉스 HBM4 양산"
    call = calls.calls[0]
    assert call["model"] == SETTINGS["claude"]["model"]
    assert issubclass(call["output_format"], IssueSummary)
    assert call["output_config"] == {"effort": SETTINGS["effort"]["summarize"]}
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert call["fallbacks"] == "default"
    assert "temperature" not in call
    assert analyzer.usage["cache_read"] == 500 and analyzer.cost_usd() > 0


@pytest.mark.parametrize("reply", [claude_ok(None, stop_reason="refusal"),
                                   claude_ok(None, parse_error=ValueError("잘린 JSON"))])
def test_claude_bad_responses_are_single_failures_but_still_billed(reply):
    client, _ = claude_client(reply)
    analyzer = Analyzer(CLAUDE, client=client)
    with pytest.raises(AIError):
        analyzer.summarize(ISSUE, {})
    assert analyzer.usage["output"] == 200


def test_claude_credit_error_stops_ai():
    err = status_error(anthropic, "BadRequestError", 400, "Your credit balance is too low to access the Anthropic API.")
    client, _ = claude_client(err)
    with pytest.raises(AIStopped):
        Analyzer(CLAUDE, client=client).summarize(ISSUE, {})


def test_claude_auth_error_stops_ai():
    client, _ = claude_client(status_error(anthropic, "AuthenticationError", 401, "invalid x-api-key"))
    with pytest.raises(AIStopped):
        Analyzer(CLAUDE, client=client).summarize(ISSUE, {})
