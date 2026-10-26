"""F1 뉴스 수집: RSS를 가져와 기사 목록으로 만든다.

기사 원문 페이지는 가져오지 않는다. RSS에 담긴 제목·요약문(일부 매체는 본문)만 쓰고,
그 텍스트는 AI 요약에만 사용할 뿐 저장·게시하지 않는다.
"""

import email.utils
import hashlib
import html
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import courlan
import feedparser
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from agent.config import Config, Source

log = logging.getLogger(__name__)

USER_AGENT = "SemiconNewsAgent/0.1 (+https://github.com/smyz1203/semicon-news-agent)"
TIMEOUT = (5, 15)  # (연결, 응답) 초
MAX_FEED_BYTES = 5 * 1024 * 1024
MAX_TEXT_CHARS = 4000
FUTURE_TOLERANCE = timedelta(minutes=15)


@dataclass
class Article:
    key: str            # 정규화한 URL의 해시 (중복 판단용)
    url: str
    title: str
    source: str         # 화면에 보여줄 매체명
    source_id: str
    lang: str
    published_at: datetime  # UTC
    text: str           # RSS 요약문/본문. 메모리에서만 사용하고 저장하지 않음
    filter: bool        # True면 관련성 키워드 검사 대상


# ── 공개 함수 ───────────────────────────────────────────────


def collect(cfg: Config, since: datetime, now: datetime) -> tuple[list[Article], dict]:
    """모든 매체에서 since 이후 기사를 모은다. (기사 목록, 매체별 결과)를 돌려준다."""
    session = make_session()
    max_age = timedelta(hours=cfg.settings["lookback"]["max_age_hours"])
    oldest = max(since, now - max_age)

    articles: list[Article] = []
    results: dict[str, dict] = {}
    for source in _all_sources(cfg):
        try:
            content = fetch(session, source.url)
            items = parse_feed(content, source, now)
        except Exception as e:  # 한 매체가 실패해도 나머지는 계속
            log.warning("수집 실패 %s: %s", source.id, e)
            results[source.id] = {"ok": False, "error": str(e)[:200], "count": 0}
            continue
        fresh = [a for a in items if a.published_at >= oldest]
        results[source.id] = {"ok": True, "count": len(fresh), "total": len(items)}
        articles.extend(fresh)
        log.info("수집 %-18s 전체 %3d건, 새 기사 %3d건", source.id, len(items), len(fresh))
    return articles, results


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": USER_AGENT,
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    })
    retry = Retry(total=2, backoff_factor=1, status_forcelist=(429, 500, 502, 503, 504),
                  allowed_methods=["GET"], respect_retry_after_header=True)
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.mount("http://", HTTPAdapter(max_retries=retry))
    return session


def fetch(session: requests.Session, url: str) -> bytes:
    resp = session.get(url, timeout=TIMEOUT)
    resp.raise_for_status()
    if len(resp.content) > MAX_FEED_BYTES:
        raise ValueError(f"피드가 너무 큼 ({len(resp.content)} bytes)")
    return resp.content


def parse_feed(content: bytes, source: Source, now: datetime) -> list[Article]:
    # bytes를 그대로 넘겨야 feedparser가 문자 인코딩을 스스로 판단함
    parsed = feedparser.parse(content)
    if not parsed.entries:
        if parsed.bozo:
            raise ValueError(f"피드 해석 실패: {parsed.bozo_exception}")
        return []

    articles = []
    for entry in parsed.entries:
        link = (entry.get("link") or "").strip()
        title = clean_text(entry.get("title") or "")
        if not link or not title:
            continue
        name = source.name
        if source.id.startswith("gnews-"):
            # Google 뉴스: 제목 끝의 " - 매체명"을 떼고, 실제 매체명을 씀
            name = (entry.get("source") or {}).get("title") or name
            title = re.sub(r"\s+-\s+[^-]+$", "", title)
        published = parse_datetime(entry.get("published") or entry.get("updated") or "", source.tz)
        if published is None or published > now + FUTURE_TOLERANCE:
            published = now
        articles.append(Article(
            key=url_key(link),
            url=link,
            title=title,
            source=name,
            source_id=source.id,
            lang=source.lang,
            published_at=published,
            text=_entry_text(entry),
            filter=source.filter,
        ))
    return articles


def parse_datetime(raw: str, default_tz: str) -> datetime | None:
    """RSS 날짜 문자열을 UTC datetime으로. 시간대 표시가 없으면 default_tz로 간주."""
    raw = raw.strip()
    if not raw:
        return None
    tz_name = default_tz
    if raw.upper().endswith("KST"):
        raw, tz_name = raw[:-3].strip(), "Asia/Seoul"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        try:
            dt = email.utils.parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(tz_name))
    return dt.astimezone(timezone.utc)


def canonical_url(url: str) -> str:
    """추적용 파라미터·www·#조각을 없앤 비교용 URL."""
    try:
        url = courlan.normalize_url(url, strict=False)
    except Exception:
        pass
    parts = urlsplit(url)
    host = re.sub(r"^(www|m)\.", "", parts.netloc.lower())
    path = parts.path.rstrip("/") or "/"
    return urlunsplit(("https", host, path, parts.query, ""))


def url_key(url: str) -> str:
    return hashlib.sha1(canonical_url(url).encode("utf-8")).hexdigest()[:16]


def clean_text(value: str) -> str:
    """HTML 태그·엔티티를 없애고 공백을 정리."""
    value = re.sub(r"</?(?:p|br|div|li|ul|ol|h\d|tr|td|blockquote)\b[^>]*>", " ", value, flags=re.IGNORECASE)
    value = re.sub(r"<[^>]+>", "", value)  # <b>, <a> 같은 글자 안의 태그는 공백 없이 제거
    value = html.unescape(html.unescape(value))  # 이중 인코딩된 &amp;quot; 대비
    return re.sub(r"\s+", " ", value).strip()


# ── 내부 함수 ───────────────────────────────────────────────


def _entry_text(entry) -> str:
    content = entry.get("content")
    if content:
        text = clean_text(content[0].get("value", ""))
        if text:
            return text[:MAX_TEXT_CHARS]
    return clean_text(entry.get("summary") or "")[:MAX_TEXT_CHARS]


def _all_sources(cfg: Config) -> list[Source]:
    sources = list(cfg.sources)
    gnews = cfg.google_news
    if gnews.get("enabled"):
        for i, q in enumerate(gnews.get("queries", []), start=1):
            ko = q["lang"] == "ko"
            params = "hl=ko&gl=KR&ceid=KR:ko" if ko else "hl=en-US&gl=US&ceid=US:en"
            url = f"https://news.google.com/rss/search?q={quote_plus(q['q'] + ' when:1d')}&{params}"
            sources.append(Source(id=f"gnews-{q['lang']}-{i}", name="Google 뉴스", url=url,
                                  lang=q["lang"], filter=True, tz="UTC"))
    return sources
