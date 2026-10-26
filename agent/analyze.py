"""F3·F4 AI 분석: AI로 기사를 이슈별로 묶고, 이슈를 요약하고 중요도를 판단한다.

AI 제공사는 config/settings.yaml의 provider로 고른다 (openai 또는 claude).
제공사별 차이(호출 방법, 오류 종류, 사용량 표기)는 OpenAIBackend / ClaudeBackend 안에 가두고,
나머지(무엇을 묻고, 응답을 어떻게 검증하는지)는 Analyzer가 공통으로 처리한다.
"""

import logging
import re

from agent import prompts
from agent.dedupe import Group
from agent.models import ClusterResult, IssueSummary
from agent.store import to_iso

log = logging.getLogger(__name__)

MAX_FAIL_STREAK = 3
COPY_WINDOW = 40  # 요약이 기사 본문을 공백 빼고 이 글자 수 이상 그대로 옮기면 거부

# 공개 저장소에 올라가는 필드라, 프롬프트 지시와 별개로 코드에서 길이를 강제함
MAX_LEN = {"headline": 60, "summary": 120, "reason": 150, "tag": 30, "company": 30}

# 제공사별로 API 키를 읽는 환경변수 이름
KEY_ENV = {"openai": "OPENAI_API_KEY", "claude": "ANTHROPIC_API_KEY"}

# OpenAI가 크레딧 소진·사용 한도 초과를 알리는 오류 코드 (기다려도 풀리지 않으므로 AI를 멈춤)
OPENAI_QUOTA_CODES = {"insufficient_quota", "credit_balance_exhausted", "organization_spend_limit_exceeded",
                      "project_spend_limit_exceeded", "organization_usage_limit_exceeded"}


class AIStopped(Exception):
    """이번 실행에서 더 이상 AI를 부를 수 없음 (API 키·크레딧·사용 한도·모델 문제, 연속 실패)."""


class AIError(Exception):
    """이 호출 하나만 실패함 (거절, 응답 형식 오류, 일시적 서버 오류)."""


class CallFailed(Exception):
    """제공사 백엔드가 '이 호출만 실패'를 알릴 때 쓰는 내부 예외. Analyzer가 AIError로 바꿈.

    응답은 받았지만 쓸 수 없는 경우(잘림, 거절, 형식 오류)에도 요금은 나가므로 usage를 함께 넘긴다.
    """

    def __init__(self, message: str, usage: dict | None = None):
        super().__init__(message)
        self.usage = usage


def _json_body(raw) -> dict:
    """원시 HTTP 응답의 JSON 본문 (사용량을 읽기 위해). 읽지 못하면 빈 dict."""
    try:
        body = raw.http_response.json()
        return body if isinstance(body, dict) else {}
    except Exception:
        return {}


# ── 제공사별 호출 ───────────────────────────────────────────────


class OpenAIBackend:
    """OpenAI Responses API + 구조화 출력(Pydantic)."""

    def __init__(self, settings: dict, client=None):
        import openai  # 쓰는 제공사의 패키지만 불러옴
        self.openai = openai
        self.cfg = settings["openai"]
        self.model = self.cfg["model"]
        self.client = client if client is not None else openai.OpenAI(
            timeout=float(settings["limits"]["ai_request_timeout_seconds"]), max_retries=2)

    def parse(self, *, system: str, user: str, output_format, max_tokens: int, effort: str):
        openai = self.openai
        kwargs = {
            "model": self.model,
            "instructions": system,
            "input": user,
            "text_format": output_format,
            "max_output_tokens": max_tokens,
            "store": False,                     # OpenAI 서버에 응답을 저장하지 않음
            "prompt_cache_key": "semicon-news",  # 같은 지시문을 반복할 때 캐시가 잘 맞도록
        }
        if self.cfg.get("reasoning"):
            kwargs["reasoning"] = {"effort": effort}

        # 원시 응답으로 받아서, JSON 해석이 실패해도 사용량(=요금)은 집계할 수 있게 함
        try:
            raw = self.client.responses.with_raw_response.parse(**kwargs)
        except (openai.AuthenticationError, openai.PermissionDeniedError, openai.NotFoundError) as e:
            log.error("API 오류: %s", _brief(e))
            raise AIStopped(f"API 키 또는 모델 설정 문제 ({type(e).__name__})") from e
        except openai.RateLimitError as e:
            if e.code in OPENAI_QUOTA_CODES or e.type == "insufficient_quota":
                log.error("API 오류: %s", _brief(e))
                raise AIStopped("크레딧 부족 또는 사용 한도 초과") from e
            raise CallFailed(_brief(e)) from e
        except openai.APIError as e:  # 잘못된 요청, 연결·서버 오류
            raise CallFailed(_brief(e)) from e

        usage = _openai_usage(_json_body(raw).get("usage") or {})
        try:
            resp = raw.parse()
        except ValueError as e:
            # 출력이 max_output_tokens에서 잘리면 SDK가 JSON 해석 중 pydantic ValidationError(ValueError)를 냄
            raise CallFailed(_brief(e), usage) from e

        if resp.status == "incomplete":
            reason = getattr(resp.incomplete_details, "reason", None)
            raise CallFailed(f"응답이 끝까지 생성되지 않음 ({reason})", usage)
        if any(getattr(part, "type", "") == "refusal"
               for item in resp.output for part in (getattr(item, "content", None) or [])):
            raise CallFailed("AI가 요청을 거절함", usage)
        if resp.output_parsed is None:
            raise CallFailed("응답을 JSON으로 해석하지 못함", usage)
        return resp.output_parsed, usage


class ClaudeBackend:
    """Anthropic Messages API + 구조화 출력(Pydantic)."""

    FALLBACK_BETA = "server-side-fallback-2026-07-01"

    def __init__(self, settings: dict, client=None):
        import anthropic  # 쓰는 제공사의 패키지만 불러옴
        self.anthropic = anthropic
        self.cfg = settings["claude"]
        self.model = self.cfg["model"]
        # 응답이 멈춘 요청 하나가 실행 시간을 다 잡아먹지 않도록 요청마다 시간 제한
        self.client = client if client is not None else anthropic.Anthropic(
            timeout=float(settings["limits"]["ai_request_timeout_seconds"]), max_retries=2)

    def parse(self, *, system: str, user: str, output_format, max_tokens: int, effort: str):
        anthropic = self.anthropic
        kwargs = {
            "model": self.model,
            "max_tokens": max_tokens,
            # 같은 실행 안에서 반복되는 지시문은 캐시해서 비용을 줄임
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": user}],
            "output_format": output_format,
            "output_config": {"effort": effort},
        }
        if self.cfg.get("server_fallback"):
            # 요청이 거절되면 서버가 다른 모델로 자동 재시도
            kwargs["betas"] = [self.FALLBACK_BETA]
            kwargs["fallbacks"] = "default"

        # 원시 응답으로 받아서, JSON 해석이 실패해도 사용량(=요금)은 집계할 수 있게 함
        try:
            raw = self.client.beta.messages.with_raw_response.parse(**kwargs)
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError, anthropic.NotFoundError) as e:
            log.error("API 오류: %s", _brief(e))
            raise AIStopped(f"API 키 또는 모델 설정 문제 ({type(e).__name__})") from e
        except anthropic.BadRequestError as e:
            if any(word in str(e).lower() for word in ("credit", "billing", "spend")):
                log.error("API 오류: %s", _brief(e))
                raise AIStopped("크레딧 부족 또는 사용 한도 초과") from e
            raise CallFailed(_brief(e)) from e
        except anthropic.RateLimitError as e:
            if "spend" in str(e).lower():
                log.error("API 오류: %s", _brief(e))
                raise AIStopped("사용 한도 초과") from e
            raise CallFailed(_brief(e)) from e
        except anthropic.APIError as e:  # 연결·서버 오류
            raise CallFailed(_brief(e)) from e

        usage = _claude_usage(_json_body(raw).get("usage") or {})
        try:
            resp = raw.parse()
        except ValueError as e:  # 출력이 잘려 JSON 해석 실패
            raise CallFailed(_brief(e), usage) from e

        if resp.stop_reason == "refusal":
            raise CallFailed("AI가 요청을 거절함", usage)
        if resp.stop_reason == "max_tokens":
            raise CallFailed("출력이 max_tokens에서 잘림", usage)
        if resp.parsed_output is None:
            raise CallFailed("응답을 JSON으로 해석하지 못함", usage)
        return resp.parsed_output, usage


def _openai_usage(u: dict) -> dict:
    # input_tokens에는 캐시에서 읽은 토큰·캐시에 새로 쓴 토큰이 포함되어 있으므로 나눠서 집계
    # (output_tokens에는 추론 토큰이 포함되며, 출력 단가로 과금됨)
    details = u.get("input_tokens_details") or {}
    cached = details.get("cached_tokens") or 0
    written = details.get("cache_write_tokens") or 0
    return {"input": max(0, (u.get("input_tokens") or 0) - cached - written), "output": u.get("output_tokens") or 0,
            "cache_write": written, "cache_read": cached}


def _claude_usage(u: dict) -> dict:
    return {"input": u.get("input_tokens") or 0, "output": u.get("output_tokens") or 0,
            "cache_write": u.get("cache_creation_input_tokens") or 0,
            "cache_read": u.get("cache_read_input_tokens") or 0}


BACKENDS = {"openai": OpenAIBackend, "claude": ClaudeBackend}


# ── 공통 분석 로직 ──────────────────────────────────────────────


class Analyzer:
    def __init__(self, settings: dict, client=None):
        self.s = settings
        self.provider = settings["provider"]
        if self.provider not in BACKENDS:
            raise ValueError(f"settings.yaml의 provider는 {list(BACKENDS)} 중 하나여야 함: {self.provider}")
        self.backend = BACKENDS[self.provider](settings, client)
        self.usage = {"calls": 0, "input": 0, "output": 0, "cache_write": 0, "cache_read": 0}
        self._fail_streak = 0

    @property
    def model(self) -> str:
        return self.backend.model

    # ── F3 이슈 묶기·분류 ─────────────────────────────────────

    def cluster(self, groups: list[Group], active_issues: list[dict]) -> dict[int, str | None]:
        """묶음마다 배정할 곳을 돌려준다: 기존 이슈 id, 'new:<키>'(새 이슈), None(관련 없음)."""
        if not groups:
            return {}
        articles = [{
            "id": f"a{i}",
            "source": g.lead.source,
            "lang": g.lead.lang,
            "published": to_iso(g.lead.published_at),
            "title": g.lead.title,
            "snippet": g.lead.text[:200],
        } for i, g in enumerate(groups)]
        issues = [{
            "id": issue["id"],
            "title": issue["headline"] or issue["articles"][0]["title"],
            "companies": issue["companies"],
        } for issue in active_issues]

        result = self._parse(
            system=prompts.CLUSTER_SYSTEM,
            user=prompts.cluster_user(articles, issues),
            output_format=ClusterResult,
            max_tokens=self.s["limits"]["cluster_max_tokens"],
            effort=self.s["effort"]["cluster"],
        )
        ids = [a["id"] for a in articles]
        resolved = resolve_assignments(result, ids, {i["id"] for i in issues})
        return {int(aid[1:]): target for aid, target in resolved.items()}

    # ── F4 요약·중요도 ────────────────────────────────────────

    def summarize(self, issue: dict, texts: dict[str, str]) -> dict:
        """이슈 하나를 요약한다. texts는 이번 실행에서 수집한 기사 본문 (기사 key → 텍스트)."""
        limits = self.s["limits"]
        # 본문이 있는 최신 기사부터 고름 (이전 실행의 기사는 본문을 저장하지 않으므로 제목만 있음)
        arts = sorted(issue["articles"], key=lambda a: (a["key"] in texts, a["published_at"]), reverse=True)
        articles = [{
            "source": a["source"],
            "lang": a["lang"],
            "published": a["published_at"],
            "title": a["title"],
            "text": texts.get(a["key"], "")[:limits["summary_input_chars"]],
        } for a in arts[:limits["summary_articles_per_issue"]]]

        result = self._parse(
            system=prompts.summarize_system(self.s),
            user=prompts.summarize_user(articles),
            output_format=IssueSummary,
            max_tokens=limits["summarize_max_tokens"],
            effort=self.s["effort"]["summarize"],
        )
        cleaned = clean_summary(result, self.s["categories"])
        if copies_source(cleaned["summary"] + [cleaned["importance_reason"]], [a["text"] for a in articles]):
            # API 문제가 아니므로 연속 실패 횟수에는 넣지 않음. 이 이슈만 다음 실행에서 다시 시도
            raise AIError("요약이 기사 문장을 그대로 옮김")
        return cleaned

    # ── 비용 ─────────────────────────────────────────────────

    def cost_usd(self) -> float:
        p = self.s[self.provider]["pricing_per_mtok"]
        u = self.usage
        total = (u["input"] * p["input"] + u["output"] * p["output"]
                 + u["cache_write"] * p.get("cache_write", p["input"]) + u["cache_read"] * p["cache_read"])
        return round(total / 1_000_000, 4)

    # ── 호출 + 실패 집계 ─────────────────────────────────────

    def _parse(self, **kwargs):
        # 예외 메시지는 공개 저장소(runs.json)에 남으므로 오류 종류만 기록하고, 자세한 내용은 로그로만
        try:
            parsed, usage = self.backend.parse(**kwargs)
        except CallFailed as e:
            self._count(e.usage)
            raise self._failed(e) from e
        self._count(usage)
        self._fail_streak = 0
        return parsed

    def _count(self, usage: dict | None) -> None:
        self.usage["calls"] += 1
        for k, v in (usage or {}).items():
            self.usage[k] += v

    def _failed(self, err: Exception) -> Exception:
        self._fail_streak += 1
        log.warning("AI 호출 실패 (%d회 연속): %s", self._fail_streak, err)
        if self._fail_streak >= MAX_FAIL_STREAK:
            return AIStopped(f"AI 호출이 {MAX_FAIL_STREAK}회 연속 실패")
        return AIError(str(err))


def _brief(err: Exception) -> str:
    return f"{type(err).__name__}: {str(err)[:200]}"


# ── 응답 검증 (AI 응답을 그대로 믿지 않고 코드로 확인) ────────────


def resolve_assignments(result: ClusterResult, article_ids: list[str],
                        issue_ids: set[str]) -> dict[str, str | None]:
    """AI 배정 결과를 검증한다. 빠진 기사·모르는 id는 새 이슈로, 중복 배정은 첫 번째만 인정."""
    out: dict[str, str | None] = {}
    valid = set(article_ids)
    for a in result.assignments:
        if a.article_id not in valid or a.article_id in out:
            continue
        if not a.relevant:
            out[a.article_id] = None
        elif a.issue_id in issue_ids:
            out[a.article_id] = a.issue_id
        else:
            out[a.article_id] = "new:" + (a.issue_id.strip() or a.article_id)
    for aid in article_ids:
        out.setdefault(aid, "new:" + aid)
    return out


def clean_summary(s: IssueSummary, categories: list[str]) -> dict:
    lines = [_cut(line, MAX_LEN["summary"]) for line in s.summary if line.strip()][:3]
    headline = _cut(s.headline, MAX_LEN["headline"])
    if not headline or not lines:
        raise AIError("요약이 비어 있음")
    return {
        "headline": headline,
        "summary": lines,
        "category": s.category if s.category in categories else "기타",
        "tags": [_cut(t, MAX_LEN["tag"]) for t in _unique(s.tags)[:6]],
        "companies": [_cut(c, MAX_LEN["company"]) for c in _unique(s.companies)[:8]],
        "importance": min(5, max(1, s.importance)),
        "importance_reason": _cut(s.importance_reason, MAX_LEN["reason"]),
    }


def copies_source(outputs: list[str], sources: list[str], window: int = COPY_WINDOW) -> bool:
    """AI가 쓴 문장 안에 기사 본문과 똑같은 구간(공백 제외 window글자 이상)이 있는지."""
    corpus = [_squash(t) for t in sources if t]
    for out in map(_squash, outputs):
        for i in range(0, len(out) - window + 1):
            piece = out[i:i + window]
            if any(piece in src for src in corpus):
                return True
    return False


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


def _cut(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _unique(values: list[str]) -> list[str]:
    seen, out = set(), []
    for v in (x.strip() for x in values):
        if v and v.lower() not in seen:
            seen.add(v.lower())
            out.append(v)
    return out
