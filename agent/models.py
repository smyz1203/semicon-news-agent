"""AI 응답 형식(JSON 스키마). Claude가 반드시 이 형식으로 답하도록 강제한다."""

from pydantic import BaseModel, Field


class Assignment(BaseModel):
    article_id: str = Field(description="입력 기사의 id (예: a3)")
    relevant: bool = Field(description="반도체·디스플레이 산업과 직접 관련 있으면 true")
    issue_id: str = Field(description="기존 이슈 id, 또는 새 이슈 키(new-1, new-2 …). relevant가 false면 빈 문자열")


class ClusterResult(BaseModel):
    assignments: list[Assignment]


class IssueSummary(BaseModel):
    headline: str = Field(description="한국어 헤드라인, 40자 이내")
    summary: list[str] = Field(description="한국어 요약 정확히 3문장, 각 80자 이내")
    category: str = Field(description="주어진 카테고리 목록 중 하나")
    tags: list[str] = Field(description="핵심 키워드 3~6개")
    companies: list[str] = Field(description="관련 기업 이름 (한국어 표기)")
    importance: int = Field(description="중요도 1~5")
    importance_reason: str = Field(description="중요도 판단 이유 한 문장")
