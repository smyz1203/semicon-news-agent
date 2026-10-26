"""AI 응답 형식(JSON 스키마). AI가 반드시 이 형식으로 답하도록 강제한다."""

from typing import Annotated, Literal

from pydantic import BaseModel, Field, create_model


class ArticleFacts(BaseModel):
    article_id: str = Field(description="입력 기사의 id (예: a3)")
    # relevant보다 먼저 쓰게 해서, 사건을 정리한 뒤 관련성을 판단하게 함
    event: str = Field(description="기사의 핵심 사건 한 줄. 한국어로 '누가 무엇을' (예: SK하이닉스, HBM4 양산 시작)")
    relevant: bool = Field(description="반도체·디스플레이 산업이나 이 산업 기업의 소식이면 true")


class TriageResult(BaseModel):
    articles: list[ArticleFacts]


class SameEvent(BaseModel):
    pair_id: str = Field(description="입력 짝의 id (예: p3)")
    # same보다 먼저 쓰게 해서, 두 사건을 비교한 뒤 판단하게 함
    reason: str = Field(description="두 사건을 비교한 한 줄")
    same: bool = Field(description="같은 사건이 확실하면 true, 다르거나 애매하면 false")


class SameEventResult(BaseModel):
    pairs: list[SameEvent]


class TypeCheck(BaseModel):
    """기본점이 높은 사건 유형을 줘도 되는지 확인 (settings.yaml의 importance.strict_min_score)."""
    # fits보다 먼저 쓰게 해서, 핵심 사건과 유형 설명을 비교한 뒤 판단하게 함
    reason: str = Field(description="핵심 사건을 한 줄로 쓰고 유형 설명과 비교")
    fits: bool = Field(description="이 유형에 확실히 해당하면 true, 애매하거나 해당 없음에 걸리면 false")


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
