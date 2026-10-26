"""중요도 계산: AI가 고른 사실(사건 유형·직접 당사자·확정 여부)과 보도 매체 수로 1~5점을 매긴다.

    중요도 = 사건 유형 기본점 + 가감점(관심 기업, 여러 매체 보도, 미확정) → 1~5점으로 맞춤

기준표는 config/settings.yaml의 importance에 있다. 같은 사실이면 언제나 같은 점수가 나오고,
계산식이 그대로 판단 근거(importance_reason)가 되므로 '왜 이 점수인가'에 답할 수 있다.
"""

from agent.dedupe import CompanyMatcher

MIN_SCORE, MAX_SCORE = 1, 5


class Scorer:
    def __init__(self, settings: dict):
        imp = settings["importance"]
        self.base = {t["name"]: t["score"] for t in imp["event_types"]}
        self.adjust = imp["adjust"]
        self.wide_coverage_sources = imp["wide_coverage_sources"]
        self.watch = imp["watch_companies"]
        self.strict_min_score = imp.get("strict_min_score", float("inf"))
        # 'SK hynix'처럼 다른 표기로 적어도 관심 기업으로 알아보도록 기업 이름 사전의 별칭을 씀
        self.watch_matcher = CompanyMatcher({name: settings["companies"].get(name, [name]) for name in self.watch})

    def needs_classification(self, issue: dict) -> bool:
        """요약은 끝났는데 사건 유형이 없거나 지금 기준표에 없는 이슈 (예전 방식으로 점수를 매긴 이슈, 유형 목록을 바꾼 경우),
        또는 기본점이 높은 유형(strict_min_score 이상)을 받았지만 아직 '정말 그 유형인지' 확인하지 않은 이슈.

        다시 요약할 이슈(needs_summary)는 요약하면서 분류도 하므로 뺀다.
        """
        if issue.get("status") != "summarized" or issue.get("needs_summary"):
            return False
        event_type = issue.get("event_type")
        return event_type not in self.base or (self.base[event_type] >= self.strict_min_score
                                               and not issue.get("type_checked"))

    def score(self, issue: dict) -> tuple[int, str] | None:
        """(중요도, 판단 근거). 사건 유형을 모르면 None."""
        result = self.calculate(issue)
        return (result[0], result[2]) if result else None

    def calculate(self, issue: dict) -> tuple[int, int, str] | None:
        """(중요도, 조정 전 점수, 판단 근거). 사건 유형을 모르면 None.

        조정 전 점수 = 기본점 + 가감점 (1~5점으로 맞추기 전). 중요도가 같은 이슈끼리 정렬할 때 쓴다.
        예: 신제품 5점 + 관심 기업 +1 = 6 → 중요도 5점이지만, 조정 전 점수가 5인 이슈보다 위.
        """
        event_type = issue.get("event_type")
        if event_type not in self.base:
            return None
        adjustments = []
        watched = self.watched(issue.get("parties", []))
        if watched:
            adjustments.append((f"관심 기업({', '.join(watched)})", self.adjust["watch_company"]))
        sources = len({a["source"] for a in issue["articles"]})
        if sources >= self.wide_coverage_sources:
            adjustments.append((f"매체 {sources}곳 보도", self.adjust["wide_coverage"]))
        if issue.get("confirmed") is False:
            adjustments.append(("미확정", self.adjust["unconfirmed"]))
        adjustments = [(label, value) for label, value in adjustments if value]  # 설정에서 0으로 끈 항목은 뺌

        base = self.base[event_type]
        total = base + sum(value for _, value in adjustments)
        final = min(MAX_SCORE, max(MIN_SCORE, total))
        # 예: "수주·공급 3점, 관심 기업(삼성전자) +1 → 4점"
        reason = ", ".join([f"{event_type} {base}점"] + [f"{label} {value:+d}" for label, value in adjustments])
        if adjustments or final != total:
            reason += f" → {final}점"
            if final != total:
                reason += "(최고점)" if total > final else "(최저점)"
        return final, total, reason

    def watched(self, parties: list[str]) -> list[str]:
        """직접 당사자 중 관심 기업 (대표 이름, 설정에 적은 순서)."""
        found = set()
        for party in parties:
            found |= self.watch_matcher.find(party)
        return [name for name in self.watch if name in found]

    def apply(self, issues: list[dict]) -> None:
        """사건 유형을 아는 이슈의 중요도·판단 근거를 기준표로 다시 계산한다 (AI 호출 없음).

        매번 다시 계산하므로 기준표 숫자를 바꾸거나 다른 매체 기사가 붙으면 바로 반영된다.
        사건 유형을 모르는 이슈(분류 전)는 원래 값을 그대로 둔다.
        """
        for issue in issues:
            result = self.calculate(issue)
            if result:
                issue["importance"], issue["importance_raw"], issue["importance_reason"] = result
