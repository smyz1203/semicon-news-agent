"""F3·F4 AI 분석: Claude로 기사를 이슈별로 묶고, 이슈를 요약하고 중요도를 판단한다."""

import logging

import anthropic

from agent import prompts
from agent.dedupe import Group
from agent.models import ClusterResult, IssueSummary
from agent.store import to_iso

log = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_FAIL_STREAK = 3


class AIStopped(Exception):
    """이번 실행에서 더 이상 AI를 부를 수 없음 (API 키·크레딧·사용 한도·모델 문제, 연속 실패)."""


class AIError(Exception):
    """이 호출 하나만 실패함 (거절, 응답 형식 오류, 일시적 서버 오류)."""


class Analyzer:
    def __init__(self, settings: dict, client: anthropic.Anthropic | None = None):
        self.s = settings
        self.client = client if client is not None else anthropic.Anthropic()
        self.usage = {"calls": 0, "input": 0, "output": 0, "cache_write": 0, "cache_read": 0}
        self._fail_streak = 0

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
        return clean_summary(result, self.s["categories"])

    # ── 비용 ─────────────────────────────────────────────────

    def cost_usd(self) -> float:
        p = self.s["pricing_per_mtok"]
        u = self.usage
        total = (u["input"] * p["input"] + u["output"] * p["output"]
                 + u["cache_write"] * p["cache_write"] + u["cache_read"] * p["cache_read"])
        return round(total / 1_000_000, 4)

    # ── API 호출 ─────────────────────────────────────────────

    def _parse(self, *, system: str, user: str, output_format, max_tokens: int, effort: str):
        kwargs = {
            "model": self.s["model"],
            "max_tokens": max_tokens,
            # 같은 실행 안에서 반복되는 지시문은 캐시해서 비용을 줄임
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": user}],
            "output_format": output_format,
            "output_config": {"effort": effort},
        }
        if self.s.get("server_fallback"):
            # 요청이 거절되면 서버가 다른 모델로 자동 재시도
            kwargs["betas"] = [FALLBACK_BETA]
            kwargs["fallbacks"] = "default"

        try:
            resp = self.client.beta.messages.parse(**kwargs)
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError, anthropic.NotFoundError) as e:
            raise AIStopped(f"API 키 또는 모델 설정 문제: {e}") from e
        except anthropic.BadRequestError as e:
            if any(word in str(e).lower() for word in ("credit", "billing", "spend")):
                raise AIStopped(f"크레딧 부족 또는 사용 한도 초과: {e}") from e
            raise self._failed(e) from e
        except anthropic.RateLimitError as e:
            if "spend" in str(e).lower():
                raise AIStopped(f"사용 한도 초과: {e}") from e
            raise self._failed(e) from e
        except (anthropic.APIError, ValueError) as e:  # 연결·서버 오류, 응답 해석 실패
            raise self._failed(e) from e

        self.usage["calls"] += 1
        u = resp.usage
        self.usage["input"] += u.input_tokens or 0
        self.usage["output"] += u.output_tokens or 0
        self.usage["cache_write"] += getattr(u, "cache_creation_input_tokens", 0) or 0
        self.usage["cache_read"] += getattr(u, "cache_read_input_tokens", 0) or 0

        if resp.stop_reason == "refusal":
            raise self._failed(AIError("AI가 요청을 거절함"))
        if resp.stop_reason == "max_tokens":
            raise self._failed(AIError("출력이 max_tokens에서 잘림"))
        if resp.parsed_output is None:
            raise self._failed(AIError("응답을 JSON으로 해석하지 못함"))
        self._fail_streak = 0
        return resp.parsed_output

    def _failed(self, err: Exception) -> Exception:
        self._fail_streak += 1
        log.warning("AI 호출 실패 (%d회 연속): %s", self._fail_streak, err)
        if self._fail_streak >= MAX_FAIL_STREAK:
            return AIStopped(f"AI 호출이 {MAX_FAIL_STREAK}회 연속 실패: {err}")
        return AIError(str(err))


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
    lines = [line.strip() for line in s.summary if line.strip()][:3]
    headline = s.headline.strip()
    if not headline or not lines:
        raise AIError("요약이 비어 있음")
    return {
        "headline": headline[:80],
        "summary": lines,
        "category": s.category if s.category in categories else "기타",
        "tags": _unique(s.tags)[:6],
        "companies": _unique(s.companies)[:8],
        "importance": min(5, max(1, s.importance)),
        "importance_reason": s.importance_reason.strip(),
    }


def _unique(values: list[str]) -> list[str]:
    seen, out = set(), []
    for v in (x.strip() for x in values):
        if v and v.lower() not in seen:
            seen.add(v.lower())
            out.append(v)
    return out
