"""F2 1차 거르기: AI를 쓰기 전에 규칙으로 중복·무관한 기사를 걸러낸다 (비용 0원).

- 이미 본 URL 제거
- 관련성 키워드가 없는 기사 제거 (일반 매체만)
- 거의 같은 제목 + 같은 회사인 기사끼리 한 묶음(Group)으로
- 묶음이 진행 중인 기존 이슈와 거의 같으면 바로 그 이슈에 배정
"""

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import timedelta

from agent.collect import Article

# 제목 앞뒤의 머리표: [단독], (종합2보), 【속보】 등
_TAG_WORDS = r"(?:단독|속보|종합\s*\d*\s*보?|\d+\s*보|포토|사진|영상|그래픽|인터뷰|르포|특징주|게시판|인사|부고)"
_TAG_RE = re.compile(r"[\[\(【<]\s*" + _TAG_WORDS + r"\s*[\]\)】>]")
_LEADING_BRACKET_RE = re.compile(r"^\s*[\[【][^\]】]{1,15}[\]】]\s*")
_HANJA = {"美": "미국 ", "中": "중국 ", "日": "일본 ", "韓": "한국 ", "臺": "대만 ",
          "英": "영국 ", "獨": "독일 ", "北": "북한 ", "印": "인도 ", "佛": "프랑스 "}


@dataclass
class Group:
    """1차 거르기에서 같은 기사로 본 묶음. AI에는 대표 기사(첫 번째)만 보여준다."""
    articles: list[Article]
    norm: str
    companies: frozenset[str]
    issue_id: str | None = None  # 규칙으로 기존 이슈에 바로 배정된 경우

    @property
    def lead(self) -> Article:
        return self.articles[0]


# ── 텍스트 매처 ─────────────────────────────────────────────


class CompanyMatcher:
    """기사 제목에서 회사를 찾아 대표 이름으로 돌려준다 (예: 'SK hynix' → 'SK하이닉스')."""

    def __init__(self, companies: dict[str, list[str]]):
        self._patterns: list[tuple[str, re.Pattern]] = []
        for name, aliases in companies.items():
            for alias in aliases:
                self._patterns.append((name, _alias_pattern(alias)))

    def find(self, text: str) -> frozenset[str]:
        return frozenset(name for name, pat in self._patterns if pat.search(text))


class KeywordMatcher:
    """반도체·디스플레이 관련 단어가 하나라도 있는지 검사."""

    def __init__(self, keywords: dict[str, list[str]], extra_names: list[str]):
        terms = keywords.get("ko", []) + keywords.get("en", []) + extra_names
        self._patterns = [_keyword_pattern(t) for t in terms]

    def is_relevant(self, text: str) -> bool:
        return any(p.search(text) for p in self._patterns)


def _alias_pattern(alias: str) -> re.Pattern:
    if alias.isascii():
        # 영문은 단어 경계를 지킴 ('Intel'이 'Intelligence'에 걸리지 않게)
        # 짧은 대문자 약어(TEL, LGD, AMD 등)는 대소문자까지 일치해야 함
        flags = 0 if (alias.isupper() and len(alias) <= 4) else re.IGNORECASE
        return re.compile(r"(?<![A-Za-z0-9])" + re.escape(alias) + r"(?![A-Za-z0-9])", flags)
    return re.compile(re.escape(alias), re.IGNORECASE)


def _keyword_pattern(term: str) -> re.Pattern:
    if term.isascii():
        # 앞쪽만 단어 경계 ('chip'이 'chips', 'chipmaker'에는 걸리고 'microchip'은 'chip'으로 안 걸림)
        return re.compile(r"(?<![A-Za-z])" + re.escape(term), re.IGNORECASE)
    return re.compile(re.escape(term), re.IGNORECASE)


# ── 제목 정규화·유사도 ──────────────────────────────────────


def normalize_title(title: str) -> str:
    t = unicodedata.normalize("NFKC", title)
    t = _TAG_RE.sub(" ", t)
    t = _LEADING_BRACKET_RE.sub(" ", t)
    for hanja, hangul in _HANJA.items():
        t = t.replace(hanja, hangul)
    t = t.lower()
    return re.sub(r"[^0-9a-z가-힣]+", " ", t).strip()


def similarity(a: str, b: str) -> float:
    """두 정규화 제목의 글자 2개 단위(bigram) 자카드 유사도 (0~1)."""
    sa, sb = _bigrams(a), _bigrams(b)
    union = sa | sb
    return len(sa & sb) / len(union) if union else 0.0


def _bigrams(s: str) -> set[str]:
    s = s.replace(" ", "")
    return {s[i:i + 2] for i in range(len(s) - 1)} or {s}


# ── 거르기 단계 ─────────────────────────────────────────────


def drop_seen(articles: list[Article], seen: dict) -> list[Article]:
    """이미 처리한 URL과, 이번 수집 안에서 겹친 URL을 뺀다."""
    out, keys = [], set()
    for a in articles:
        if a.key in seen or a.key in keys:
            continue
        keys.add(a.key)
        out.append(a)
    return out


def drop_irrelevant(articles: list[Article], matcher: KeywordMatcher) -> list[Article]:
    # 본문 전체를 보면 지나가듯 언급된 단어에도 걸리므로 제목과 앞부분만 본다
    return [a for a in articles if not a.filter or matcher.is_relevant(f"{a.title} {a.text[:200]}")]


def group_articles(articles: list[Article], companies: CompanyMatcher,
                   threshold: float, max_hours: float) -> list[Group]:
    """거의 같은 제목이면서 언급된 회사가 같은 기사끼리 묶는다."""
    groups: list[Group] = []
    window = timedelta(hours=max_hours)
    for a in sorted(articles, key=lambda x: x.published_at):
        norm, comps = normalize_title(a.title), companies.find(a.title)
        for g in groups:
            if (comps == g.companies
                    and abs(a.published_at - g.lead.published_at) <= window
                    and similarity(norm, g.norm) >= threshold):
                g.articles.append(a)
                break
        else:
            groups.append(Group(articles=[a], norm=norm, companies=comps))
    return groups


def attach_to_issues(groups: list[Group], active_issues: list[dict],
                     companies: CompanyMatcher, threshold: float) -> None:
    """묶음이 기존 이슈의 기사 제목과 거의 같으면 그 이슈에 바로 배정한다 (group.issue_id 설정).

    이슈 안의 기사가 꼬리를 물고 번지지 않도록, 각 이슈의 처음 5건·마지막 5건 제목과만 비교한다.
    """
    refs = []
    for issue in active_issues:
        arts = issue["articles"]
        for art in arts[:5] + arts[-5:]:
            refs.append((issue["id"], normalize_title(art["title"]), companies.find(art["title"])))
    for g in groups:
        for issue_id, norm, comps in refs:
            if comps == g.companies and similarity(g.norm, norm) >= threshold:
                g.issue_id = issue_id
                break
