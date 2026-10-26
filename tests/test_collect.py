from datetime import datetime, timezone

from agent.collect import canonical_url, clean_text, parse_datetime, parse_feed, url_key
from agent.config import Source

NOW = datetime(2026, 10, 26, 10, 0, tzinfo=timezone.utc)
KR = Source(id="test-kr", name="테스트일보", url="https://example.kr/rss", lang="ko", filter=True, tz="Asia/Seoul")


def test_parse_datetime_without_timezone_uses_source_tz():
    # 디일렉·KIPOST 형식: 시간대 표시 없음 → 한국 시간으로 보고 UTC로 변환
    assert parse_datetime("2026-10-26 18:08:47", "Asia/Seoul") == datetime(2026, 10, 26, 9, 8, 47, tzinfo=timezone.utc)


def test_parse_datetime_formats():
    expected = datetime(2026, 10, 26, 7, 2, tzinfo=timezone.utc)
    assert parse_datetime("Mon, 26 Oct 2026 16:02:00 +0900", "UTC") == expected
    assert parse_datetime("Mon, 26 Oct 2026 07:02:00 GMT", "Asia/Seoul") == expected
    assert parse_datetime("2026-10-26T16:02:00+09:00", "UTC") == expected
    assert parse_datetime("2026-10-26T07:02:00Z", "Asia/Seoul") == expected
    assert parse_datetime("Mon, 26 Oct 2026 16:02:00 KST", "UTC") == expected
    assert parse_datetime("Mon, 26 Oct 2026 07:02:00 -0000", "Asia/Seoul") == expected  # -0000 = UTC


def test_parse_datetime_invalid():
    assert parse_datetime("", "UTC") is None
    assert parse_datetime("어제 오후", "UTC") is None


def test_canonical_url_removes_tracking_and_www():
    a = canonical_url("http://www.thelec.kr/news/articleView.html?idxno=64275&utm_source=x&fbclid=y#top")
    b = canonical_url("https://thelec.kr/news/articleView.html?idxno=64275")
    assert a == b
    # 기사 번호 같은 실제 파라미터는 지우지 않음
    assert url_key("https://thelec.kr/news/articleView.html?idxno=1") != url_key("https://thelec.kr/news/articleView.html?idxno=2")


def test_clean_text_strips_html_and_entities():
    assert clean_text("<b>삼성</b>전자 &amp;quot;HBM&amp;quot;  공급") == '삼성전자 "HBM" 공급'
    assert clean_text("<p>첫 문단</p><p>둘째 문단<br/>끝</p>") == "첫 문단 둘째 문단 끝"


RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
<channel><title>t</title>
<item>
  <title><![CDATA[[단독] SK하이닉스, HBM4 양산]]></title>
  <link>https://example.kr/a/1?utm_source=rss</link>
  <description><![CDATA[<p>요약문 &amp; 내용</p>]]></description>
  <pubDate>2026-10-26 18:00:00</pubDate>
</item>
<item>
  <title>본문 있는 기사</title>
  <link>https://example.kr/a/2</link>
  <description>짧은 요약</description>
  <content:encoded><![CDATA[<p>전체 본문입니다.</p>]]></content:encoded>
  <pubDate>Mon, 26 Oct 2026 08:00:00 GMT</pubDate>
</item>
<item>
  <title>미래 날짜 기사</title>
  <link>https://example.kr/a/3</link>
  <pubDate>2027-01-01 00:00:00</pubDate>
</item>
<item><title>링크 없는 기사</title></item>
</channel></rss>""".encode("utf-8")


def test_parse_feed():
    items = parse_feed(RSS, KR, NOW)
    assert [a.title for a in items] == ["[단독] SK하이닉스, HBM4 양산", "본문 있는 기사", "미래 날짜 기사"]
    first, second, future = items
    assert first.published_at == datetime(2026, 10, 26, 9, 0, tzinfo=timezone.utc)
    assert first.text == "요약문 & 내용"
    assert first.source == "테스트일보" and first.lang == "ko" and first.filter is True
    assert second.text == "전체 본문입니다."  # content:encoded가 있으면 그걸 씀
    assert future.published_at == NOW  # 미래 시각은 시간대 오류로 보고 현재 시각으로


def test_same_outlet_feeds_share_name():
    # The Elec(thelec.net)은 디일렉(thelec.kr)의 영문판: 이름이 같아야 '매체 N곳 보도'에서 한 곳으로 셈
    from agent.config import load_config
    assert {s.name for s in load_config().sources if "thelec." in s.url} == {"디일렉"}


def test_parse_feed_google_news_uses_real_publisher():
    gnews = Source(id="gnews-ko-1", name="Google 뉴스", url="u", lang="ko", filter=True, tz="UTC")
    rss = """<?xml version="1.0"?><rss version="2.0"><channel><item>
      <title>삼성전자 HBM 공급 - 한국경제</title>
      <link>https://news.google.com/rss/articles/CBMi123?oc=5</link>
      <source url="https://www.hankyung.com">한국경제</source>
      <pubDate>Mon, 26 Oct 2026 08:00:00 GMT</pubDate>
    </item></channel></rss>""".encode("utf-8")
    [item] = parse_feed(rss, gnews, NOW)
    assert item.title == "삼성전자 HBM 공급"
    assert item.source == "한국경제"
