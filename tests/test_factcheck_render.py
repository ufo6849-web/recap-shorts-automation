"""사실 확인 작업의 그림·배치·가드·프롬프트 시험 (유료 호출 없음).

뉴스 카드 모양(1080x810, 날짜 유무, 제목 3줄, 글꼴에 없는 글자), 뉴스 카드 배치 점수, 민감 검사 건너뛰기,
가드의 사실 아님 주장 삭제, 프롬프트 블록. 기존 시험은 각 모듈 시험 파일에 둔다.
"""
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw

from recap_shorts import pipeline as P
from recap_shorts.collect import news_card as NC
from recap_shorts.images import arrange, sensitive
from recap_shorts.render import layout
from recap_shorts.script.guard import run_guard
from recap_shorts.script.prompt import RULES, build_prompt

FIX = Path(__file__).parent / "fixtures" / "script"
TOPIC = json.loads((FIX / "topic_sample.json").read_text(encoding="utf-8"))

TITLE = "‘〇〇우유’인데 원유는 안 넣는다?… 가공유 깜깜이 함량 표시 논란"
LONG = "정부가 " * 30 + "발표한 내용"
FACT = "원유 없는 가공유가 문제로 떠올랐다"
EV1 = "상대팀 투수가 갑자기 쥐가 나서 마운드에서 주저앉음"           # topic_sample 원문 구절
FALSE_Q = "올해 일흔 두 살이라는데 공이 시속 70킬로밖에 안 나왔음"   # topic_sample 원문 구절(사실 아님 주장의 근거)
EV_FALSE = "공이 시속 70킬로밖에 안 나왔음"                          # FALSE_Q 의 일부 → 같은 근거


def _font():
    return layout.load_font(NC.HEAD_SIZE)


def _draw():
    return ImageDraw.Draw(Image.new("RGB", (4, 4)))


# ---- 뉴스 카드 (collect/news_card.py)

def test_card_is_picture_box_size_with_and_without_date(tmp_path):
    a = NC.draw_card(TITLE, "조선비즈", "2026-11-02", tmp_path / "a.png")
    b = NC.draw_card(TITLE, "조선비즈", None, tmp_path / "b" / "b.png")       # 폴더가 없으면 만든다
    for p in (a, b):
        with Image.open(p) as im:
            assert im.size == (1080, 810) and im.mode == "RGB"
    assert a.read_bytes() != b.read_bytes()                                  # 날짜는 줄 때만 그린다


def test_long_title_is_three_lines_with_ellipsis(tmp_path):
    lines = NC._wrap(_draw(), NC._printable(LONG, _font()), _font(), NC.TEXT_W, NC.HEAD_LINES)
    assert len(lines) == 3 and lines[-1].endswith("…")
    assert all(_draw().textlength(x, font=_font()) <= NC.TEXT_W for x in lines)
    assert Image.open(NC.draw_card(LONG, "조선비즈", "2026-11-02", tmp_path / "long.png")).size == (1080, 810)


def test_circled_zero_becomes_lookalike_not_tofu():
    font = _font()
    cleaned = NC._printable("‘〇〇우유’", font)
    assert cleaned == "‘○○우유’"                                              # 〇 → ○ (글꼴에 ○ 있음)
    notdef = NC._ink(font, NC._NOTDEF)
    assert NC._ink(font, "〇") == notdef                                       # 전제: Pretendard 에 〇 없음(빈 상자)
    assert all(NC._ink(font, c) != notdef for c in cleaned if not c.isspace())


def test_glyphs_missing_everywhere_are_dropped_and_present_ones_kept():
    assert NC._printable("가😀漢나", _font()) == "가나"
    assert NC._printable("①·A", _font()) == "①·A"


def test_cli_entry_point_takes_datetime(tmp_path):
    p = NC.draw_news_card("가짜 제목", "가짜일보", datetime(2026, 10, 5, 1, 30), tmp_path / "old.png")
    assert Image.open(p).size == (1080, 810)
    assert NC.fmt_date(datetime(2026, 10, 5)) == "2026.10.05" and NC.FontNotFound is layout.FontNotFoundError


# ---- 뉴스 카드 배치 (images/arrange.py)

NEWS = {"file": "assets/news/card_01.png", "kind": "cap", "role": "news", "fact_id": "F1",
        "fact_text": FACT, "outlet": "축산신문", "status": "ok"}


def test_news_match_rules():
    assert arrange._news_match(FACT, FACT)                                  # 같다
    assert arrange._news_match(FACT + "는 글이 올라왔대", FACT)               # 문장이 사실을 품는다
    assert arrange._news_match("원유 없는 가공유가", FACT)                     # 사실이 문장을 품는다
    assert not arrange._news_match("가공유 가격은 올랐다", FACT)
    assert not arrange._news_match("", FACT) and not arrange._news_match(FACT, None)   # 빈 글은 맞지 않는다


def test_news_card_scores_only_the_body_sentence_with_its_fact():
    sents = [{"index": 0, "role": "hook", "hint": "", "evidence": FACT},              # 훅은 0 (근거가 같아도)
             {"index": 1, "role": "body", "hint": "", "evidence": FACT + "는 글이 올라왔대"},
             {"index": 2, "role": "body", "hint": "", "evidence": "공장 사진이 함께 올라왔다"},
             {"index": 3, "role": "body", "hint": "", "evidence": None}]
    sc = arrange.heuristic_scores(sents, [NEWS])
    assert [row[0] for row in sc] == [0.0, 0.95, 0.0, 0.0]


@pytest.mark.parametrize("evidence", ["분석된 176개 제품 가운데 27개가 원유 0%였다",
                                      "분석된176개제품가운데27개가원유0%였다!",
                                      "분석된 176개 제품 가운데 27개가 원유 0%였다는 보도"])
def test_news_matches_another_fact_from_same_source(evidence):
    other = "분석된 176개 제품 가운데 27개가 원유 0%였다"
    card = dict(NEWS, fact_ids=["F1", "F3"], fact_texts=[FACT, other])
    sentence = {"index": 0, "role": "body", "evidence": evidence}
    assert arrange.heuristic_scores([sentence], [card]) == [[0.95]]


@pytest.mark.parametrize("role,expected", [("body", 0.85), ("hook", 0.0)])
def test_news_title_overlap_without_matching_fact(role, expected):
    title = "가공유 함량 표시 제도 개선 검토"
    card = dict(NEWS, title=title)
    sentence = {"index": 0, "role": role, "text": title, "evidence": title}
    assert not arrange._news_match(title, FACT)
    assert arrange.heuristic_scores([sentence], [card])[0][0] == pytest.approx(expected)


@pytest.mark.parametrize("reference,expected", [("aa bb", 0.0), ("aa bb cc", 0.5),
                                               ("aa bb cc dd ee ff", 0.65)])
def test_news_overlap_threshold_and_proportional_score(reference, expected):
    sentence = {"index": 0, "role": "body", "text": "aa bb cc dd ee ff gg hh ii jj"}
    card = dict(NEWS, fact_texts=[reference])
    assert arrange.heuristic_scores([sentence], [card])[0][0] == pytest.approx(expected)


def test_news_unrelated_sentence_scores_zero_with_all_metadata():
    card = dict(NEWS, fact_texts=[FACT, "가공유 함량 표시 제도 개선"], title=TITLE)
    sentence = {"index": 0, "role": "body", "text": "야구 경기에서 삼진을 잡았다", "evidence": "투수의 승리"}
    assert arrange.heuristic_scores([sentence], [card]) == [[0.0]]


@pytest.mark.parametrize("count,expected", [(3, [0, 2, 1]), (2, [0, 2])])
def test_news_fallback_adds_to_best_body_and_replaces_lowest_score(count, expected):
    images = [{"file": "cap", "kind": "cap"}, {"file": "ai", "kind": "ai"}, NEWS]
    infos = [{"index": 0, "role": "hook", "text": FACT},
             {"index": 1, "role": "body", "text": "야구 경기"},
             {"index": 2, "role": "body", "text": FACT},
             {"index": 3, "role": "closing", "text": FACT}]
    pick = [[0], [0], [0, 1], [0]]
    P._ensure_news_card(pick, infos, images, [[0.9, 0.2, 0.0]] * 4, [1, 1, count, 1])
    assert pick == [[0], [0], expected, [0]]


@pytest.mark.parametrize("shared,placed", [(0, False), (2, False), (3, False), (4, True)])
def test_news_fallback_requires_strictly_more_than_point_fifteen(shared, placed):
    words = "aa bb cc dd ee ff gg hh ii jj kk ll mm nn oo pp qq rr ss tt".split()
    card = dict(NEWS, fact_texts=[" ".join(words[:shared])])
    infos = [{"index": 0, "role": "body", "text": " ".join(words)}]
    pick = [[0]]
    assert arrange.news_overlap(infos[0], card) == shared / 20
    P._ensure_news_card(pick, infos, [{"kind": "cap"}, card], [[0.7, 0.0]], [2])
    assert pick == ([[0, 1]] if placed else [[0]])


def test_news_fallback_leaves_existing_news_placement_alone():
    images = [{"kind": "cap"}, NEWS, dict(NEWS, file="other.png")]
    infos = [{"index": 0, "role": "body", "text": FACT}]
    pick = [[0, 1]]
    P._ensure_news_card(pick, infos, images, [[0.7, 0.95, 0.95]], [3])
    assert pick == [[0, 1]]


@pytest.mark.parametrize("role,status", [("hook", "ok"), ("closing", "ok"), ("body", "error")])
def test_news_fallback_only_uses_successful_cards_in_body(role, status):
    infos = [{"index": 0, "role": role, "text": FACT}]
    pick = [[0]]
    P._ensure_news_card(pick, infos, [{"kind": "cap"}, dict(NEWS, status=status)], [[0.7, 0.0]], [2])
    assert pick == [[0]]


def test_build_plan_applies_news_fallback_after_assignment(tmp_path, monkeypatch):
    for folder in ("script_final", "voice"):
        (tmp_path / folder).mkdir()
    (tmp_path / "script_final/script.json").write_text(json.dumps({"sentences": [{"text": FACT, "evidence": FACT}]}))
    (tmp_path / "voice/timings.json").write_text(json.dumps({
        "duration": 3, "sentences": [{"index": 1, "role": "body", "text": FACT, "start": 0, "end": 3}]}))
    monkeypatch.setattr(P, "load_assets", lambda job: [{"file": "cap.png", "kind": "cap"}, NEWS])
    monkeypatch.setattr(arrange, "plan_with_ai_cap", lambda *args: [[0]])
    doc = P.build_plan(SimpleNamespace(dir=tmp_path, options={}), SimpleNamespace())
    assert set(doc["plan"]["1"]) == {"cap.png", NEWS["file"]}
    assert doc["scores"]["1"][NEWS["file"]] == 0.95
    assert "캡처 2컷" in doc["note"]


def test_kind_of_news_and_other_kinds_unchanged():
    assert arrange.kind_of({"kind": "cap", "role": "news"}) == "news"
    assert arrange.kind_of({"kind": "cap"}) == "cap"
    assert arrange.kind_of({"kind": "cap", "role": "focus"}) == "focus"
    assert arrange.kind_of({"kind": "ai"}) == "ai"


def test_off_script_drops_other_story_captures_but_never_news():
    sents = [{"index": 0, "role": "hook", "hint": ""},
             {"index": 1, "role": "body", "hint": "AI 장면: x / 캡처: 첨부 그림 3번", "evidence": FACT}]
    atts = [{"n": 1, "kind": "SNS 글 캡처", "summary": "주말 아르바이트 시급 계산 방법"},
            {"n": 3, "kind": "SNS 글 캡처", "summary": "원유 없는 가공유 이야기"}]
    imgs = [{"file": "c1", "kind": "cap", "attachment": 1}, {"file": "c3", "kind": "cap", "attachment": 3}, NEWS]
    assert arrange.off_script(sents, imgs, atts) == {0}


def test_assign_counts_news_like_a_capture_not_ai():
    kinds = ["news", "cap", "focus", "ai", "ai"]
    as_cap = ["cap", "cap", "focus", "ai", "ai"]
    scores = [[0.95, 0.3, 0.1, 0.4, 0.2], [0.0, 0.5, 0.2, 0.9, 0.1], [0.0, 0.2, 0.8, 0.3, 0.6]]
    counts = [2, 2, 2]
    for share in (0.0, 0.34):
        assert arrange.assign(scores, counts, kinds, None, share) == arrange.assign(scores, counts, as_cap, None, share)
    plan = arrange.plan_with_ai_cap(scores, counts, kinds, None, 0.2)
    assert plan == arrange.plan_with_ai_cap(scores, counts, as_cap, None, 0.2)
    assert 0 in plan[0]                                                    # 뉴스 카드는 맞는 문장에 들어간다
    assert 0 not in plan[2]                                                # 맞지 않는 문장에는 0점이라 안 들어간다


# ---- 민감 검사 건너뛰기 (images/sensitive.py)

def test_sensitive_skips_news_cards_and_checks_captures(tmp_path):
    for rel in ("assets/news/card_01.png", "assets/cap/c.png"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (120, 90), (200, 20, 20)).save(tmp_path / rel)     # 빨강: 가짜 모델이 strong 으로 본다
    ents = [{"file": "assets/news/card_01.png", "kind": "cap", "role": "news", "status": "ok", "needs_review": False,
             "review_reason": None},
            {"file": "assets/cap/c.png", "kind": "cap", "status": "ok", "needs_review": False, "review_reason": None}]
    seen = []

    def read_fn(images, instruction):
        seen.append(len(images))
        return {"items": [{"i": i, "level": "none", "kind": "none", "reason": ""} for i in range(1, len(images) + 1)]}
    before = (tmp_path / ents[0]["file"]).read_bytes()
    sensitive.run(tmp_path, ents, read_fn=read_fn, cfg={**sensitive.DEFAULTS})
    assert seen == [1]                                                     # 카드는 묻지 않고 캡처 한 장만 묻는다
    assert "sensitive" not in ents[0] and (tmp_path / ents[0]["file"]).read_bytes() == before
    assert "sensitive" in ents[1]


# ---- 가드: 사실 아님 주장과 같은 근거 (script/guard.py)

def S(text, ev=None, hint="일러스트"):
    return {"text": text, "evidence": ev, "source_id": None, "emphasis": None, "image_hint": hint}


def _script(sents, hook="오늘자 동호회 레전드 경기 터졌다", closing="이게 말이 되냐 진짜"):
    return {"title_lines": ["일흔 두 살 투수가", "삼진 두 개를 잡았다"], "hook": hook, "sentences": sents,
            "closing": closing, "flags": []}


def _topic_with_false(quote=FALSE_Q):
    return dict(TOPIC, false_claims=[{"id": "C2", "claim": "투수가 시속 70킬로를 던졌다", "source_quote": quote}])


def test_guard_deletes_sentence_sharing_evidence_with_false_claim_and_flags_it():
    sents = [S("상대 투수가 쥐가 났다는데", EV1), S("그 투수가 시속 칠십 킬로를 던졌다는데", EV_FALSE),
             S("그러더니 분위기가 이상해졌다는데")]
    out, chk = run_guard(_script(sents), _topic_with_false())
    assert [s["text"] for s in out["sentences"]] == ["상대 투수가 쥐가 났다는데", "그러더니 분위기가 이상해졌다는데"]
    assert chk["deleted"] == [{"index": 1, "text": "그 투수가 시속 칠십 킬로를 던졌다는데", "reason": "사실 아님 주장(C2)과 같은 근거"}]
    flag = "사실 확인: 사실 아님 주장(C2)과 같은 근거라 문장 2 삭제"
    assert flag in out["flags"] and flag in chk["flags"] and not chk["ok"]


def test_guard_false_claim_overlap_needs_six_characters():
    sents = [S("공이 꽤 빨랐다는데", "70킬로")]                             # 정규화 4자: 원문에는 있지만 겹침 기준 미달
    out, chk = run_guard(_script(sents), _topic_with_false())
    assert chk["deleted"] == [] and [s["text"] for s in out["sentences"]] == ["공이 꽤 빨랐다는데"]


def test_guard_without_false_claims_is_unchanged():
    sents = [S("상대 투수가 쥐가 났다는데", EV1), S("그 투수가 시속 150킬로를 던졌다고 함", "시속 150킬로를 던졌음"),
             S("그러더니 분위기가 이상해졌다는데")]
    base = run_guard(_script(sents), TOPIC)
    assert run_guard(_script(sents), dict(TOPIC, false_claims=[])) == base
    assert not any("사실 확인" in f for f in base[1]["flags"])


# ---- 프롬프트 블록 (script/prompt.py)

def test_prompt_has_fact_blocks_only_when_present():
    plain = build_prompt(TOPIC)
    assert build_prompt(dict(TOPIC, facts=[], false_claims=[], unverified_claims=[])) == plain
    for label in ("사실 아님 주장 false_claims", "확인되지 않은 주장 unverified_claims", "[사실 확인 규칙]"):
        assert label not in plain
    topic = dict(TOPIC, facts=[{"id": "F1", "text": "축산신문 보도: 원유 없는 가공유", "evidence": "원유 없는"}],
                 false_claims=[{"id": "C2", "claim": "원유 없이 우유라는 말", "source_quote": FALSE_Q}],
                 unverified_claims=[{"id": "C3", "claim": "매출이 세 배로 늘었다"}])
    p = build_prompt(topic)
    for must in ("원문과 확인된 사실(facts)이 다르면 facts를 따른다", "사실 아님 주장(false_claims)은 사실처럼 말하지 않는다",
                 "확인되지 않은 주장(unverified_claims)의 수치·주장은 쓰지 않는다", "(C2) 원유 없이 우유라는 말",
                 "(C3) 매출이 세 배로 늘었다", "(F1) 축산신문 보도"):
        assert must in p, must
    assert p.index("[확인된 사실") < p.index("[사실 아님 주장") < p.index("[확인되지 않은 주장") < p.index("[사실 확인 규칙") < p.index(RULES.splitlines()[0])
