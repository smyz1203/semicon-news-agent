from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from agent.analyze import AIError, AIStopped, Analyzer, clean_summary, copies_source, resolve_assignments
from agent.config import load_config
from agent.models import Assignment, ClusterResult, IssueSummary

SETTINGS = load_config().settings


class FakeMessages:
    """client.beta.messages.parse 자리에 들어가는 가짜. 미리 정한 응답이나 예외를 차례로 돌려준다."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def fake_client(*replies):
    messages = FakeMessages(replies)
    return SimpleNamespace(beta=SimpleNamespace(messages=messages)), messages


def ok(parsed, stop_reason="end_turn"):
    usage = SimpleNamespace(input_tokens=1000, output_tokens=200,
                            cache_creation_input_tokens=0, cache_read_input_tokens=500)
    return SimpleNamespace(parsed_output=parsed, stop_reason=stop_reason, usage=usage)


def api_error(cls, status, message):
    response = httpx2.Response(status, request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages"))
    return cls(message, response=response, body=None)


SUMMARY = IssueSummary(headline="SK하이닉스 HBM4 양산", summary=["첫째.", "둘째.", "셋째.", "넷째."],
                       category="메모리", tags=["HBM4", "hbm4", " AI "], companies=["SK하이닉스"],
                       importance=9, importance_reason="차세대 기술 양산")
ISSUE = {"id": "c1", "headline": "", "companies": [], "articles": [
    {"key": "k1", "title": "제목1", "source": "디일렉", "lang": "ko", "published_at": "2026-10-26T00:00:00Z"},
]}


def test_resolve_assignments_validates_ai_output():
    result = ClusterResult(assignments=[
        Assignment(article_id="a0", relevant=True, issue_id="c-old"),   # 기존 이슈
        Assignment(article_id="a1", relevant=True, issue_id="new-1"),
        Assignment(article_id="a2", relevant=True, issue_id="new-1"),   # 같은 새 이슈
        Assignment(article_id="a3", relevant=False, issue_id=""),       # 관련 없음
        Assignment(article_id="a3", relevant=True, issue_id="new-9"),   # 중복 배정 → 무시
        Assignment(article_id="zz", relevant=True, issue_id="new-2"),   # 모르는 기사 → 무시
        Assignment(article_id="a4", relevant=True, issue_id="c-unknown"),  # 모르는 이슈 → 새 이슈
    ])
    out = resolve_assignments(result, ["a0", "a1", "a2", "a3", "a4", "a5"], {"c-old"})
    assert out == {"a0": "c-old", "a1": "new:new-1", "a2": "new:new-1", "a3": None,
                   "a4": "new:c-unknown", "a5": "new:a5"}  # a5는 AI가 빠뜨림 → 혼자 새 이슈


def test_clean_summary():
    out = clean_summary(SUMMARY, SETTINGS["categories"])
    assert out["summary"] == ["첫째.", "둘째.", "셋째."]
    assert out["importance"] == 5
    assert out["tags"] == ["HBM4", "AI"]
    bad = SUMMARY.model_copy(update={"category": "스포츠"})
    assert clean_summary(bad, SETTINGS["categories"])["category"] == "기타"
    with pytest.raises(AIError):
        clean_summary(SUMMARY.model_copy(update={"summary": [" "]}), SETTINGS["categories"])


def test_clean_summary_enforces_length_limits():
    long = SUMMARY.model_copy(update={"headline": "가" * 200, "summary": ["나" * 500], "tags": ["다" * 99],
                                      "importance_reason": "라" * 999})
    out = clean_summary(long, SETTINGS["categories"])
    assert len(out["headline"]) <= 60 and out["headline"].endswith("…")
    assert len(out["summary"][0]) <= 120
    assert len(out["tags"][0]) <= 30
    assert len(out["importance_reason"]) <= 150


def test_copies_source():
    body = "SK하이닉스는 26일 경기 이천 본사에서 HBM4 12단 제품의 양산 출하를 시작했다고 밝혔다. 회사는 내년 생산량을 두 배로 늘린다."
    copied = "SK하이닉스는 26일 경기 이천 본사에서 HBM4 12단 제품의 양산 출하를 시작했다고 밝혔다."
    rewritten = "SK하이닉스가 HBM4 12단 양산 출하에 들어갔다."
    assert copies_source([copied], [body])
    assert not copies_source([rewritten], [body])
    assert not copies_source([copied], [""])


def test_summary_that_copies_article_is_rejected():
    body = "가나다라마바사아자차카타파하" * 5
    copied = SUMMARY.model_copy(update={"summary": [body[:60], "둘째.", "셋째."]})
    client, _ = fake_client(ok(copied))
    with pytest.raises(AIError):
        Analyzer(SETTINGS, client=client).summarize(ISSUE, {"k1": body})


def test_summarize_request_shape_and_usage():
    client, messages = fake_client(ok(SUMMARY))
    analyzer = Analyzer(SETTINGS, client=client)
    out = analyzer.summarize(ISSUE, {"k1": "본문" * 2000})
    assert out["headline"] == "SK하이닉스 HBM4 양산"
    call = messages.calls[0]
    assert call["model"] == SETTINGS["model"]
    assert call["output_format"] is IssueSummary
    assert call["output_config"] == {"effort": SETTINGS["effort"]["summarize"]}
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert call["fallbacks"] == "default"
    assert "temperature" not in call
    # 본문은 설정한 글자 수까지만 보냄
    assert "본문" * (SETTINGS["limits"]["summary_input_chars"] // 2) in call["messages"][0]["content"]
    assert "본문" * 1000 not in call["messages"][0]["content"]
    assert analyzer.usage["calls"] == 1 and analyzer.cost_usd() > 0


def test_refusal_is_a_single_failure():
    client, _ = fake_client(ok(None, stop_reason="refusal"))
    with pytest.raises(AIError):
        Analyzer(SETTINGS, client=client).summarize(ISSUE, {})


def test_credit_error_stops_ai():
    err = api_error(anthropic.BadRequestError, 400, "Your credit balance is too low to access the Anthropic API.")
    client, _ = fake_client(err)
    with pytest.raises(AIStopped):
        Analyzer(SETTINGS, client=client).summarize(ISSUE, {})


def test_auth_error_stops_ai():
    client, _ = fake_client(api_error(anthropic.AuthenticationError, 401, "invalid x-api-key"))
    with pytest.raises(AIStopped):
        Analyzer(SETTINGS, client=client).summarize(ISSUE, {})


def test_three_failures_in_a_row_stop_ai():
    err = api_error(anthropic.InternalServerError, 500, "server error")
    client, _ = fake_client(err, err, err)
    analyzer = Analyzer(SETTINGS, client=client)
    for _ in range(2):
        with pytest.raises(AIError):
            analyzer.summarize(ISSUE, {})
    with pytest.raises(AIStopped):
        analyzer.summarize(ISSUE, {})
