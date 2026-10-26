from datetime import datetime, timedelta, timezone

from agent.collect import Article, url_key
from agent.dedupe import (CompanyMatcher, KeywordMatcher, attach_to_issues, drop_irrelevant, drop_seen,
                          group_articles, normalize_title, similarity)

T0 = datetime(2026, 10, 26, 0, 0, tzinfo=timezone.utc)
COMPANIES = CompanyMatcher({
    "삼성전자": ["삼성전자", "Samsung Electronics"],
    "SK하이닉스": ["SK하이닉스", "하이닉스", "SK hynix"],
    "인텔": ["인텔", "Intel"],
    "도쿄일렉트론": ["도쿄일렉트론", "TEL"],
})


def art(title, url=None, hours=0.0, source="A", text="", filter=True):
    url = url or f"https://example.com/{abs(hash(title))}"
    return Article(key=url_key(url), url=url, title=title, source=source, source_id=source.lower(),
                   lang="ko", published_at=T0 + timedelta(hours=hours), text=text, filter=filter)


def test_normalize_title():
    assert normalize_title("[단독] 美 상무부, 中 반도체 수출 규제 (종합2보)") == "미국 상무부 중국 반도체 수출 규제"
    assert normalize_title("【속보】SK하이닉스 HBM4 양산") == "sk하이닉스 hbm4 양산"


def test_similarity():
    a = normalize_title("SK하이닉스, HBM4 세계 최초 양산 돌입")
    b = normalize_title("[속보] SK하이닉스 HBM4 세계 최초 양산 돌입")
    c = normalize_title("LG디스플레이, 8.6세대 OLED 투자 결정")
    assert similarity(a, b) > 0.9
    assert similarity(a, c) < 0.2


def test_company_matcher_word_boundaries():
    assert COMPANIES.find("Intel begins Arizona fab") == {"인텔"}
    assert COMPANIES.find("Artificial Intelligence boom") == frozenset()
    assert COMPANIES.find("TEL ships etch tools") == {"도쿄일렉트론"}
    assert COMPANIES.find("Startup moves to Tel Aviv") == frozenset()
    assert COMPANIES.find("SK hynix and 삼성전자") == {"SK하이닉스", "삼성전자"}


def test_group_merges_near_duplicate_titles():
    groups = group_articles([
        art("SK하이닉스, HBM4 세계 최초 양산 돌입", source="A"),
        art("[속보] SK하이닉스 HBM4 세계 최초 양산 돌입", source="B", hours=1),
        art("LG디스플레이, 8.6세대 OLED 투자 결정", source="C"),
    ], COMPANIES, threshold=0.6, max_hours=48)
    assert sorted(len(g.articles) for g in groups) == [1, 2]


def test_group_keeps_different_companies_apart():
    # 제목 모양이 거의 같아도 회사가 다르면 다른 이슈
    groups = group_articles([
        art("삼성전자, 3분기 영업이익 10조원 기록"),
        art("SK하이닉스, 3분기 영업이익 10조원 기록"),
    ], COMPANIES, threshold=0.6, max_hours=48)
    assert len(groups) == 2


def test_group_respects_time_window():
    groups = group_articles([
        art("SK하이닉스, HBM4 세계 최초 양산 돌입"),
        art("SK하이닉스, HBM4 세계 최초 양산 돌입", url="https://other.com/x", hours=72),
    ], COMPANIES, threshold=0.6, max_hours=48)
    assert len(groups) == 2


def test_drop_seen_removes_known_and_repeated_urls():
    a = art("기사 1", url="https://example.com/1")
    b = art("기사 1 복사", url="https://www.example.com/1?utm_source=x")
    c = art("기사 2", url="https://example.com/2")
    assert drop_seen([a, b, c], seen={c.key: "2026-10-25T00:00:00Z"}) == [a]


def test_drop_irrelevant_only_checks_filtered_sources():
    kw = KeywordMatcher({"ko": ["반도체"], "en": ["chip"]}, extra_names=["SK하이닉스"])
    keep1 = art("반도체 수출 증가")
    keep2 = art("Chipmakers rally")
    keep3 = art("SK하이닉스 신임 사장")
    keep4 = art("전시회 소식", filter=False)  # 전문 섹션은 검사 안 함
    drop = art("스마트폰 판매 부진")
    assert drop_irrelevant([keep1, keep2, keep3, keep4, drop], kw) == [keep1, keep2, keep3, keep4]
    assert not kw.is_relevant("microchip-free gadget")


def test_attach_to_existing_issue():
    groups = group_articles([art("[속보] SK하이닉스 HBM4 세계 최초 양산 돌입")], COMPANIES, 0.6, 48)
    issues = [{"id": "c1", "articles": [{"title": "SK하이닉스, HBM4 세계 최초 양산 돌입"}]},
              {"id": "c2", "articles": [{"title": "삼성전자 파운드리 2나노 수주"}]}]
    attach_to_issues(groups, issues, COMPANIES, 0.6)
    assert groups[0].issue_id == "c1"
