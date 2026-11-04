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


def test_default_outlets_file_loads():
    o = N.load_outlets()
    assert o.get("yna.co.kr") == "연합뉴스"


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
