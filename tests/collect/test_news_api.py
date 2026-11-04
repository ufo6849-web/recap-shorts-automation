import json
from datetime import datetime, timezone

import pytest

from recap_shorts.collect import news_api as N
from recap_shorts.collect.news_card import FontNotFound, draw_news_card

ITEM = N.NewsItem(title='가짜 박물관 "굿즈" 인기', description="가짜 스니펫 굿즈 판매량이 & 두 배로 늘었다.",
                  originallink="https://www.yna.co.kr/view/AKR1", link="https://www.yna.co.kr/view/AKR1",
                  pub_date=datetime(2026, 10, 5, 1, 30, tzinfo=timezone.utc), outlet="연합뉴스")


def test_clean_text_strips_tags_and_entities():
    assert N.clean_text("<b>가짜</b> 박물관 &quot;굿즈&quot;  인기") == '가짜 박물관 "굿즈" 인기'


def test_outlet_subdomain_suffix():
    assert N.outlet_for("https://www.yna.co.kr/a", {"yna.co.kr": "연합뉴스"}) == "연합뉴스"
    assert N.outlet_for("https://unknown.example.com/a", {"yna.co.kr": "연합뉴스"}) == "unknown.example.com"  # 모르면 도메인 그대로


@pytest.mark.parametrize("prefix", ["", "www.", "m.", "mobile.", "www.m.mobile."])
def test_outlet_fallback_strips_leading_prefixes(prefix):
    assert N.outlet_for(f"https://{prefix.upper()}UNKNOWN.EXAMPLE.COM:8443/a", {}) == "unknown.example.com"


@pytest.mark.parametrize("host", ["www2.example.com", "mobile-news.example.com", "news.m.example.com"])
def test_outlet_fallback_preserves_other_subdomains(host):
    assert N.outlet_for(f"https://{host}/a", {}) == host


@pytest.mark.parametrize("prefix", ["www.", "m.", "mobile."])
def test_outlet_mapping_uses_normalized_host(prefix):
    outlets = {"example.com": "가짜일보", f"{prefix}example.com": "접두 제거 전 매핑"}
    assert N.outlet_for(f"https://{prefix.upper()}EXAMPLE.COM:8443/a", outlets) == "가짜일보"


@pytest.mark.parametrize("domain, outlet", [
    ("newsworks.co.kr", "뉴스웍스"), ("chuksannews.co.kr", "축산신문"),
    ("mt.co.kr", "머니투데이"), ("starnewskorea.com", "스타뉴스"), ("wikitree.co.kr", "위키트리"),
    ("insight.co.kr", "인사이트"), ("mydaily.co.kr", "마이데일리"), ("nocutnews.co.kr", "노컷뉴스"),
    ("hankookilbo.com", "한국일보"), ("kmib.co.kr", "국민일보"), ("segye.com", "세계일보"),
    ("seoul.co.kr", "서울신문"), ("munhwa.com", "문화일보"),
])
@pytest.mark.parametrize("prefix", ["", "www.", "m.", "mobile."])
def test_added_outlets_resolve_to_names(domain, outlet, prefix):
    assert N.outlet_for(f"https://{prefix}{domain}/a") == outlet


def test_default_outlets_file_loads():
    o = N.load_outlets()
    assert o.get("yna.co.kr") == "연합뉴스"


@pytest.mark.parametrize("prefix", ["", "www.", "m.", "mobile."])
def test_chosun_biz_uses_longest_domain_match(prefix):
    assert N.outlet_for("https://biz.chosun.com/distribution/food/article") == "조선비즈"
    outlets = {"chosun.com": "조선일보", "biz.chosun.com": "조선비즈"}
    for mapping in (outlets, dict(reversed(list(outlets.items())))):
        assert N.outlet_for(f"https://{prefix}biz.chosun.com/a", mapping) == "조선비즈"
        assert N.outlet_for(f"https://{prefix}chosun.com/a", mapping) == "조선일보"
        assert N.outlet_for("https://notbiz.chosun.com/a", mapping) == "조선일보"


def test_no_naver_search_left():
    """뉴스 검색 기능을 뺐으므로 뉴스 검색 API 를 부르는 길이 남아 있지 않아야 한다."""
    assert not any(n.lower().startswith(("search_news", "load_naver", "parse_naver")) for n in dir(N))


def test_find_quote_title_and_snippet_only():
    assert N.find_quote(ITEM, "판매량이 &  두배로 늘었다")
    assert N.find_quote(ITEM, "가짜 박물관 “굿즈” 인기")
    assert not N.find_quote(ITEM, "기사 본문에만 있을 법한 문장")
    assert not N.find_quote(ITEM, "")


def test_items_from_json(tmp_path):
    f = tmp_path / "n.json"
    f.write_text(json.dumps([{"title": "가짜 제목", "outlet": "가짜일보", "date": "2026-10-05", "url": "https://x.example/a"}]),
                 encoding="utf-8")
    it = N.items_from_json(f)[0]
    assert it.outlet == "가짜일보" and it.pub_date.year == 2026 and it.originallink == "https://x.example/a"


@pytest.mark.parametrize("suffix,name", [
    (" - 머니투데이", "머니투데이"), (" | 스타뉴스", "스타뉴스"),
    (" :: 공감언론 뉴시스 ::", "공감언론 뉴시스"),
])
@pytest.mark.parametrize("mapping", ["unknown", "matching", "different"])
def test_news_title_suffix_forms(tmp_path, suffix, name, mapping):
    url = "https://news.example.com/article"
    title = "기사 제목 - 중간 내용 | 앞 구분자"
    outlets = {} if mapping == "unknown" else {"example.com": name if mapping == "matching" else "다른언론"}
    expected = (title + suffix, "다른언론") if mapping == "different" else (title, name)
    assert N.title_and_outlet(title + suffix, url, outlets) == expected
    path = tmp_path / "news.json"
    path.write_text(json.dumps([{"title": title + suffix, "url": url}]), encoding="utf-8")
    item = N.items_from_json(path, outlets)[0]
    assert (item.title, item.outlet) == expected


@pytest.mark.parametrize("suffix", ["", " - 가", " | " + "가" * 21, " :: " + "가" * 21 + " ::"])
def test_news_title_without_valid_suffix_keeps_domain(suffix):
    title = "기사 제목" + suffix
    assert N.title_and_outlet(title, "https://m.unknown.example.com/a", {}) == (title, "unknown.example.com")


@pytest.mark.parametrize("name", ["가나", "가" * 20])
def test_news_title_suffix_name_length_bounds(name):
    assert N.title_and_outlet(f"기사 제목 | {name}", "https://unknown.example.com", {}) == ("기사 제목", name)


def test_news_title_registered_name_mismatch_and_explicit_outlet(tmp_path):
    assert N.title_and_outlet("기사 :: 공감언론 뉴시스 ::", "https://newsis.com/a") == (
        "기사 :: 공감언론 뉴시스 ::", "뉴시스")
    path = tmp_path / "news.json"
    path.write_text(json.dumps([{"title": "기사 | 스타뉴스", "url": "https://unknown.example.com/a",
                                 "outlet": "직접 지정한 언론사"}]), encoding="utf-8")
    item = N.items_from_json(path, {})[0]
    assert (item.title, item.outlet) == ("기사 | 스타뉴스", "직접 지정한 언론사")


def test_news_card_renders(tmp_path):
    from PIL import Image
    p = draw_news_card("가" * 120, ITEM.outlet, ITEM.pub_date, tmp_path / "c" / "card.png")
    with Image.open(p) as im:
        assert im.size == (1080, 810)
    p2 = draw_news_card(ITEM.title, "연합뉴스", ITEM.pub_date, tmp_path / "c2.png")
    assert p2.stat().st_size > 5000


def test_news_card_font_missing(tmp_path, monkeypatch):
    from recap_shorts.render import layout
    monkeypatch.setattr(layout, "FONT_CANDIDATES", [str(tmp_path / "none.otf")])
    layout.load_font.cache_clear()
    with pytest.raises(FontNotFound):
        draw_news_card("제목", "언론사", "2026.10.05", tmp_path / "x.png")
    layout.load_font.cache_clear()


def test_cli_keyword_without_news_json_skips_cards_but_continues(tmp_path):
    from recap_shorts.collect.__main__ import run
    r = run("가짜", "j", photos=0, work_root=tmp_path)
    assert ("뉴스 카드", "news_json_missing") in r["skipped"]
    assert r["news_source"] == ""


def test_cli_news_json_makes_cards_and_manifest(tmp_path):
    from recap_shorts.collect.__main__ import run
    from recap_shorts.collect.manifest import Manifest
    f = tmp_path / "n.json"
    f.write_text(json.dumps([{"title": "가짜 제목", "outlet": "가짜일보", "date": "2026-10-05", "url": "https://x.example/a"}]),
                 encoding="utf-8")
    r = run("", "j", photos=0, work_root=tmp_path, news_json=str(f))
    rec = Manifest.load(r["manifest"])[0]
    assert rec["kind"] == "news_card" and rec["source_url"] == "https://x.example/a"
    assert rec["license"] == "기사 제목 인용(출처 표기)" and rec["attribution"] == "출처: 가짜일보 (2026.10.05)"
    assert (tmp_path / "j" / rec["file"]).exists()
