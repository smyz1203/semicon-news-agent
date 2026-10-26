"""AI 응답 형식(JSON 스키마). AI가 반드시 이 형식으로 답하도록 강제한다."""

from typing import Annotated, Literal

from pydantic import BaseModel, Field, create_model


class Assignment(BaseModel):
    article_id: str = Field(description="입력 기사의 id (예: a3)")
    relevant: bool = Field(description="반도체·디스플레이 산업과 직접 관련 있으면 true")
    issue_id: str = Field(description="기존 이슈 id, 또는 새 이슈 키(new-1, new-2 …). relevant가 false면 빈 문자열")


class ClusterResult(BaseModel):
    assignments: list[Assignment]


# 중요도 계산에 쓰는 사실. 점수는 AI가 아니라 agent/score.py가 이 값으로 계산한다.
EventType = Annotated[str, Field(description="사건 유형 목록 중 정확히 하나")]
Confirmed = Annotated[bool, Field(description="이미 일어났거나 공식 발표·결정·체결된 사건이면 true, "
                                              "검토·추진·협상 중이거나 전망·루머면 false")]
Parties = Annotated[list[str], Field(description="사건의 직접 당사자 기업 (한국어 표기, 없으면 빈 목록)")]


class IssueSummary(BaseModel):
    headline: str = Field(description="한국어 헤드라인, 40자 이내")
    summary: list[str] = Field(description="한국어 요약 정확히 3문장, 각 80자 이내")
    category: str = Field(description="주어진 카테고리 목록 중 하나")
    tags: list[str] = Field(description="핵심 키워드 3~6개")
    companies: list[str] = Field(description="관련 기업 이름 (한국어 표기)")
    event_type: EventType
    confirmed: Confirmed
    parties: Parties


class IssueFacts(BaseModel):
    """이미 요약된 이슈를 다시 분류할 때 (예전 방식으로 점수를 매긴 이슈, 사건 유형 목록이 바뀐 경우)."""
    event_type: EventType
    confirmed: Confirmed
    parties: Parties


def with_event_types(model: type[BaseModel], names: list[str]) -> type[BaseModel]:
    """event_type을 settings.yaml의 사건 유형 이름 중 하나로만 답하게 제한한 모델 (JSON 스키마의 enum)."""
    field = model.model_fields["event_type"]
    return create_model(model.__name__, __base__=model,
                        event_type=(Literal[tuple(names)], Field(description=field.description)))
