"""사실 가드: script.json 의 사실 문장이 원문(source_text)에 근거를 갖는지 확인하고, 형식 규격을 검사한다.

방식은 "근거 없는 숫자·주장은 통과시키지 않는다"를 따르되,
여기서는 문장마다 붙은 evidence(원문 구절)를 원문과 직접 대조한다.

규칙
 - 사실 문장 = evidence 가 있거나, evidence 가 null 이어도 숫자·알파벳·따옴표 낱말이 들어 있는 문장.
 - 사실 문장의 evidence 가 원문에 없으면(정규화 후) 그 문장을 삭제한다.
 - evidence 가 null 인 문장은 반응·연결 문장만 허용. 숫자·알파벳이 있으면 사실 문장으로 보고 삭제.
 - 삭제하지 않고 flags 만 남기는 것: evidence 길이(10~60자) 벗어남, 음절 수·훅 길이·제목 길이 규격 벗어남,
   본문 숫자가 원문에 없음.
 - 금지 표현(구독, 좋아요 등)이 든 문장은 삭제하고 기록.
 - F9 우리 해석(check_take): 본문에 "정리·맥락·비교·관점" 문장이 하나도 없으면 flag 만 남긴다(막지 않음).
   해석 문장은 sentences[].take=true 로 표시한다. 표시가 하나도 없는 옛 대본·손으로 쓴 대본은 표시 없이 어림해서 본다.
 - F3·F4 말투 숫자(check_style): 전해 들은 끝맺음·꾸밈 연결어·'근데'·접속사로 시작하는 문장이 측정한 기준보다 많으면 flag 만.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .text import count_syllables, normalize_loose

TITLE_MAX = 14
HOOK_RANGE = (12, 20)           # 음절 수. 측정한 훅(=제목)은 대개 13~20음절
# 훅이 서술어로 끝났는지: 끝 글자(문장부호 뺀)가 반말·평서 어미면 문장으로 본다(경고만). 그 훅은 명사로 끝난다.
_HOOK_VERB_END = set("어아요다래지야네까죠냐해대돼임음함")
SYLLABLE_RANGE = (190, 220)     # 훅 + 본문 + 끝 한 줄 합
EVIDENCE_LEN = (10, 60)         # 글자 수(공백 포함)
BANNED = ("구독", "좋아요", "알림 설정", "알림설정", "팔로우", "눌러 주세요", "눌러주세요")

_DIGIT_RE = re.compile(r"\d")
_LATIN_RE = re.compile(r"[A-Za-z]")
_QUOTED_RE = re.compile(r"[\"'“”‘’「」『』《》〈〉]([^\"'“”‘’「」『』《》〈〉]{1,20})[\"'“”‘’「」『』《》〈〉]")


def looks_factual(text: str) -> bool:
    """숫자·알파벳(약어·고유명사)·따옴표로 묶은 낱말이 있으면 사실 문장으로 본다."""
    return bool(_DIGIT_RE.search(text) or _LATIN_RE.search(text) or _QUOTED_RE.search(text))


def evidence_in_source(evidence: str | None, source_text: str) -> bool:
    ev = normalize_loose(evidence or "")
    return bool(ev) and ev in normalize_loose(source_text)


FALSE_OVERLAP_MIN = 6   # 사실 아님 주장의 원문 구절과 근거가 같은지: 정규화 후 6자 이상인 쪽이 다른 쪽에 들어 있어야 한다


def _same_source(a: str, b: str) -> bool:
    return (len(a) >= FALSE_OVERLAP_MIN and a in b) or (len(b) >= FALSE_OVERLAP_MIN and b in a)


def _false_claim_of(evidence: str | None, false_quotes: list[tuple[dict, str]]) -> dict | None:
    """근거(evidence)가 사실 아님 주장의 원문 구절과 겹치면 그 주장. false_quotes = [(주장, 정규화한 source_quote)]."""
    if not evidence:
        return None
    ne = normalize_loose(evidence)
    return next((c for c, q in false_quotes if _same_source(ne, q)), None)


def _numbers(text: str) -> list[str]:
    return [m.replace(",", "") for m in re.findall(r"\d[\d,]*(?:\.\d+)?", text)]


# ---- F9 우리 해석 (유튜브 쇼츠가 남의 글 + 내레이션만으로는 독창적이지 않다고 보는 기준에 맞추기)
# 해석 문장이 원문을 거의 베낀 것인지는 "문장의 글자 3-gram 중 원문(+facts)에도 있는 비율"로 본다(공백·문장부호 뺀 한글·영숫자 기준).
# 이 값은 베낌을 걸러내는 그물일 뿐이다. 말만 바꿔 요약한 문장은 이 값이 낮게 나오므로(실제 대본 3편의 사실 문장 20개 중
# 0.04~0.32 가 15개) 거기까지는 못 잡는다 — 그건 모델이 단 take 표시와 사람 확인에 맡긴다.
#   원문을 그대로 줄여 읽은 문장  0.39 · 0.46 · 0.48 · 0.61 · 0.63   (실제 대본의 사실 문장, 모두 이 값 이상이라 '베낌')
#   직접 쓴 해석 문장 10개 예      0.03 ~ 0.18                         (정리·관점 문장이라 원문 글자와 거의 안 겹침)
TAKE_COPY_MAX = 0.35
TAKE_MIN_SYLLABLES = 12          # 이보다 짧으면 정리·관점 한 문장이 아니라 짧은 반응으로 본다
_NGRAM = 3
TAKE_FLAG_NONE = ("우리 해석 문장이 없음 — 유튜브 10월 쇼츠 변경: 남의 글+내레이션만으로는 독창적이지 않음 "
                  "(본문에 정리·맥락·비교·관점 한 문장이 필요함. 끝 한 줄 반응은 해당 안 됨)")


def as_bool(v) -> bool:
    """모델이 true 대신 "true"/"yes"/1 로 답해도 받아 준다."""
    if isinstance(v, str):
        return v.strip().lower() in ("true", "yes", "y", "1", "예", "네")
    return bool(v)


def _grams(text: str, n: int = _NGRAM) -> set[str]:
    t = normalize_loose(text)
    return {t[i:i + n] for i in range(len(t) - n + 1)}


def copy_ratio(sentence: str, source: str) -> float:
    """문장의 글자 3-gram 중 원문에도 있는 비율(0~1). 1 에 가까울수록 원문을 그대로 옮긴 문장."""
    g = _grams(sentence)
    return len(g & _grams(source)) / len(g) if g else 0.0


def check_take(sents: list[dict], haystack: str, take_deleted: bool = False) -> tuple[dict, list[str]]:
    """본문 문장(훅·끝 한 줄 제외)에 우리 해석이 있는지. (결과, flags) 를 돌려준다. 훅과 끝 한 줄은 해석으로 세지 않는다 —
    훅은 제목이고 끝 한 줄은 이야기를 받아치는 반응(F8)이라 글 내용을 다시 말한 것이기 쉽다.

    mode "marked": take=true 로 표시한 문장이 있다 -> 표시한 문장만 본다(하나라도 베낌이 아니고 12음절 이상이면 통과).
    mode "unmarked": take 필드는 있는데 true 가 하나도 없다(모델이 해석을 안 썼다) -> 바로 flag.
    mode "fallback": take 필드가 아예 없다(옛 대본·손으로 쓴 대본) -> 근거(evidence) 없는 본문 문장 중 베낌이 아니고
        12음절 이상인 것이 있으면 통과. 사실 문장은 evidence 를 달고 있어서(규칙 7) 말만 바꾼 요약은 여기서 걸러진다.
    """
    gs = _grams(haystack)
    rows = []
    for s in sents:
        g = _grams(s.get("text") or "")
        rows.append(len(g & gs) / len(g) if g else 0.0)
    marked = [i for i, s in enumerate(sents) if as_bool(s.get("take"))]
    if marked:
        mode, cand = "marked", marked
    elif any("take" in s for s in sents):
        mode, cand = "unmarked", []
    else:
        mode, cand = "fallback", [i for i, s in enumerate(sents) if not (s.get("evidence") or "").strip()]
    counted, why = [], {}
    for i in cand:
        syl = count_syllables(sents[i].get("text") or "")
        if rows[i] >= TAKE_COPY_MAX:
            why[i] = f"해석으로 표시한 {i + 1}번 문장이 원문과 {round(rows[i] * 100)}% 겹침 — 글 내용을 다시 전하는 문장은 우리 해석이 아님 (유튜브 10월 쇼츠 변경: 남의 글+내레이션만으로는 독창적이지 않음)"
        elif syl < TAKE_MIN_SYLLABLES:
            why[i] = f"해석으로 표시한 {i + 1}번 문장이 너무 짧음({syl}음절) — 정리·맥락·비교·관점을 {TAKE_MIN_SYLLABLES}음절 이상 한 문장으로 쓸 것"
        else:
            counted.append(i)
    flags: list[str] = []
    if not counted:
        if mode == "marked":
            flags.append(why[marked[0]] + (f" (해석 표시 {len(marked)}개 모두 해당)" if len(marked) > 1 else ""))
        else:
            flags.append(TAKE_FLAG_NONE + (" — 표시했던 해석 문장은 근거 문제로 삭제됨" if take_deleted else ""))
    check = {"ok": bool(counted), "mode": mode, "marked": [i + 1 for i in marked], "counted": [i + 1 for i in counted],
             "copy_max": TAKE_COPY_MAX, "min_syllables": TAKE_MIN_SYLLABLES, "overlap": [round(r, 2) for r in rows]}
    return check, flags


# ---- F3·F4 말투 숫자 (받아쓰기 비교 대본 vs 이 프로그램 대본 12편 측정값 — 아래 비율의 출처)
# 끝난 문장 중 전해 들은 꼴: 기준 3~12% / 이 프로그램 29~37%.  해라체 '~다': 기준 15~21% / 이 프로그램 0%.
# 꾸밈 연결어(1000어절당): 기준 0~1.3 / 이 프로그램의 심지어 8.6·반면 4.9·알고 보니 3.7.  '근데': 기준 1.2~5.2 / 이 프로그램 10.8~12.3.
# 전해 들은 꼴 1번·해라체 2~3문장 섞기 규칙 뒤 대본은 반말 사이에 '~했다·~한다고 한다'가 끼어 뉴스처럼 튀었다.
# 그래서 반말 하나로 맞추고(해라체가 하나라도 있으면 경고), 전해 들은 꼴은 3번까지로 풀었다.
STYLE_HEARSAY_MAX = 3            # 대본 전체에서 "~했대·~래·~더라·~했다는데" 끝맺음
STYLE_HAERA_MAX = 0              # 반말 대본에 섞인 해라체 '~했다·~였다·~한다' 끝맺음
SENTENCE_MAX_SYLLABLES = 36      # F6 은 15~30음절. 이보다 길면 경고(다시 쓴 대본에서 50음절 문장이 나온 적이 있다)
STYLE_FLOURISH_MAX = 1           # "심지어·반면·웃긴 건·알고 보니·덕분에" 합
STYLE_GEUNDE_MAX = 1
STYLE_OPENERS_MAX = 2            # 문장 첫머리 접속사
_HEARSAY_END = re.compile(r"(더라|더라고|다는데|라는데|다던데|라던데|답니다|랍니다|대요|래요)$")
_HEARSAY_PREV = set("하다이으는된한난간온산운린킨진던")   # '~하대·~으래·~는대' 처럼 '대·래' 앞에 오는 글자


def _is_hearsay(word: str) -> bool:
    """전해 들은 끝맺음인가. '대·래' 로 끝나는 명사(세대·반대·미래·침대)는 빼려고, 앞 글자가 받침 ㅆ(었대·했대·렸대)이거나
    어미 앞 글자(하대·으래·는대)일 때만 센다."""
    if _HEARSAY_END.search(word):
        return True
    if len(word) >= 2 and word[-1] in "대래":
        prev = word[-2]
        if "가" <= prev <= "힣" and (ord(prev) - 0xAC00) % 28 == 20:   # 받침 ㅆ
            return True
        return prev in _HEARSAY_PREV
    return False
# 해라체·합쇼체 평서: 끝 낱말이 '다'로 끝나면(받아쳤다·접었다·한다·이다·있습니다). 반말 대본에서 '다'로 끝나는 끝맺음은 이것뿐이다.
# 예전 목록(했다·였다·었다…)은 글자 단위라 '쳤다·렸다·겼다' 를 놓쳤다.
_HAERA_END = re.compile(r"[가-힣]다$")
_FLOURISH = ("심지어", "반면", "웃긴 건", "웃긴건", "알고 보니", "알고보니", "덕분에")
_OPENERS = ("근데", "그런데", "하지만", "그래서", "그러자", "그러면서", "그럼", "그리고", "심지어", "게다가", "결국",
            "알고 보니", "알고보니", "사실", "특히", "반면", "웃긴 건", "덕분에", "한편", "그래도", "정리하면")


def _last_word(text: str) -> str:
    ws = re.sub(r"[^가-힣\s?]", " ", text).split()
    return ws[-1] if ws else ""


def check_style(texts: list[str]) -> tuple[dict, list[str]]:
    """본문+끝 한 줄의 말투 숫자를 세고 기준을 넘으면 flag 만 남긴다(막지 않음). 훅은 명사로 끝나는 제목이라 뺀다."""
    hearsay = [i + 1 for i, t in enumerate(texts) if _is_hearsay(_last_word(t))]
    haera = [i + 1 for i, t in enumerate(texts) if _HAERA_END.search(_last_word(t))]
    joined = " " + " ".join(re.sub(r"[^가-힣\s]", " ", t) for t in texts) + " "
    flourish = {k: joined.count(f" {k} ") for k in _FLOURISH if joined.count(f" {k} ")}
    geunde = joined.count(" 근데 ")
    openers = [i + 1 for i, t in enumerate(texts) if any(t.strip().startswith(o + " ") or t.strip().startswith(o + ",")
                                                          for o in _OPENERS)]
    flags: list[str] = []
    if len(hearsay) > STYLE_HEARSAY_MAX:
        flags.append(f"전해 들은 끝맺음(~대·~래·~더라) {len(hearsay)}번 (문장 {hearsay}) — 대본 전체 {STYLE_HEARSAY_MAX}번까지")
    if len(haera) > STYLE_HAERA_MAX:
        flags.append(f"반말 사이에 '~다' 끝맺음 {len(haera)}번 (문장 {haera}) — 뉴스처럼 튀니 '~었어·~거든·~지'로")
    if sum(flourish.values()) > STYLE_FLOURISH_MAX:
        flags.append(f"꾸밈 연결어 {sum(flourish.values())}번 ({', '.join(f'{k} {v}' for k, v in flourish.items())}) — 잘 안 쓰는 표현, {STYLE_FLOURISH_MAX}번까지")
    if geunde > STYLE_GEUNDE_MAX:
        flags.append(f"'근데' {geunde}번 — {STYLE_GEUNDE_MAX}번까지")
    if len(openers) > STYLE_OPENERS_MAX:
        flags.append(f"접속사로 시작하는 문장 {len(openers)}개 (문장 {openers}) — {STYLE_OPENERS_MAX}개까지, 앞 말을 '~는데·~더니'로 받아 잇기")
    check = {"ok": not flags, "hearsay": hearsay, "haera": haera, "flourish": flourish, "geunde": geunde, "openers": openers,
             "limits": {"hearsay": STYLE_HEARSAY_MAX, "haera": STYLE_HAERA_MAX, "flourish": STYLE_FLOURISH_MAX,
                        "geunde": STYLE_GEUNDE_MAX, "openers": STYLE_OPENERS_MAX}}
    return check, flags


_QUOTE_RE = re.compile(r"'([^']{2,60})'|‘([^’]{2,60})’|\"([^\"]{2,60})\"|“([^”]{2,60})”")


def check_quotes(texts: list[str], norm_src: str) -> tuple[dict, list[str]]:
    """F10: 따옴표로 옮긴 말이 원문에 글자 그대로 있는지(공백·문장부호 무시). 없으면 지어낸 인용이라 경고(막지 않음)."""
    quotes, missing = [], []
    for i, t in enumerate(texts):
        for m in _QUOTE_RE.finditer(t or ""):
            q = next(g for g in m.groups() if g)
            quotes.append(q)
            nq = normalize_loose(q)
            if nq and nq not in norm_src:
                missing.append({"sentence": i + 1, "quote": q})
    flags = [f"원문에 없는 따옴표 말 (문장 {m['sentence']}): '{m['quote']}'" for m in missing]
    return {"ok": not missing, "count": len(quotes), "missing": missing}, flags


def narration_texts(script: dict) -> list[str]:
    """내레이션 순서: 훅 -> 본문 문장 -> 끝 한 줄. (본문 첫 문장이 훅과 같으면 중복으로 보고 한 번만)"""
    out = [script.get("hook", "")]
    for s in script.get("sentences", []):
        out.append(s["text"])
    out.append(script.get("closing", ""))
    return [t for t in out if t]


def run_guard(script: dict, topic: dict) -> tuple[dict, dict]:
    """(검사 후 script, script_check) 를 돌려준다. 입력 script 는 바꾸지 않는다."""
    src = topic.get("source_text") or ""
    facts_text = " ".join(f.get("text", "") for f in topic.get("facts") or [])
    haystack = src + " " + facts_text
    norm_src = normalize_loose(haystack)
    src_numbers = set(_numbers(haystack))

    false_quotes = [(c, normalize_loose(c.get("source_quote") or "")) for c in topic.get("false_claims") or []]

    sc = json.loads(json.dumps(script, ensure_ascii=False))
    sc.setdefault("flags", [])
    flags: list[str] = []
    deleted: list[dict] = []
    warnings: list[str] = []

    # 훅이 본문 첫 문장으로 또 들어 있으면 중복 제거
    sents = sc.get("sentences") or []
    if sents and sents[0].get("text", "").strip() == (sc.get("hook") or "").strip():
        sents = sents[1:]
        warnings.append("본문 첫 문장이 훅과 같아 제거함")

    kept = []
    take_deleted = False
    for i, s in enumerate(sents):
        text = (s.get("text") or "").strip()
        ev = s.get("evidence")
        ev = ev.strip() if isinstance(ev, str) and ev.strip() else None
        s["evidence"] = ev
        reason = None
        banned = next((b for b in BANNED if b in text), None)
        hit = None if not text or banned else _false_claim_of(ev, false_quotes)
        if not text:
            reason = "빈 문장"
        elif banned:
            reason = f"금지 표현 '{banned}'"
        elif hit:
            reason = f"사실 아님 주장({hit['id']})과 같은 근거"
        elif ev is not None:
            if normalize_loose(ev) not in norm_src:
                reason = "근거 구절이 원문에 없음"
            elif not (EVIDENCE_LEN[0] <= len(ev) <= EVIDENCE_LEN[1]):
                warnings.append(f"문장 {i}: 근거 길이 {len(ev)}자(권장 {EVIDENCE_LEN[0]}~{EVIDENCE_LEN[1]})")
            else:
                bad = [n for n in _numbers(text) if n not in src_numbers]
                if bad:
                    warnings.append(f"문장 {i}: 숫자 {bad} 가 원문에 없음 (확인 필요)")
        elif looks_factual(text):
            reason = "숫자·고유명사가 있는데 근거 없음"
        if reason:
            deleted.append({"index": i, "text": text, "reason": reason})
            take_deleted = take_deleted or as_bool(s.get("take"))
            if hit:
                flags.append(f"사실 확인: 사실 아님 주장({hit['id']})과 같은 근거라 문장 {i + 1} 삭제")
        else:
            kept.append(s)
    sc["sentences"] = kept

    # 훅·끝 줄: 숫자는 원문에 있어야 한다 (근거 필드가 없는 문장이므로 삭제 대신 flag)
    for key in ("hook", "closing"):
        t = sc.get(key) or ""
        bad = [n for n in _numbers(t) if n not in src_numbers]
        if bad:
            flags.append(f"{key}: 숫자 {bad} 가 원문에 없음")
        b = next((x for x in BANNED if x in t), None)
        if b:
            flags.append(f"{key}: 금지 표현 '{b}'")

    bad = [n for t in sc.get("title_lines") or [] for n in _numbers(t) if n not in src_numbers]
    if bad:
        flags.append(f"제목: 숫자 {bad} 가 원문에 없음 (원문 표기와 다르면 확인)")

    # 규격 검사
    checks: dict = {}
    tl = sc.get("title_lines") or []
    checks["title_lines"] = {"lens": [len(x) for x in tl], "ok": len(tl) == 2 and all(0 < len(x) <= TITLE_MAX for x in tl)}
    if not checks["title_lines"]["ok"]:
        flags.append(f"제목 규격 어긋남: {[len(x) for x in tl]}자 (2줄, 각 {TITLE_MAX}자 이내)")
    hook = sc.get("hook") or ""
    hook_syl = count_syllables(hook)
    last = hook.rstrip(" .!?…,~")[-1:]
    hook_noun = bool(last) and last not in _HOOK_VERB_END
    checks["hook"] = {"chars": len(hook), "syllables": hook_syl, "range": list(HOOK_RANGE), "ends_noun": hook_noun,
                      "ok": HOOK_RANGE[0] <= hook_syl <= HOOK_RANGE[1] and hook_noun}
    if not HOOK_RANGE[0] <= hook_syl <= HOOK_RANGE[1]:
        flags.append(f"훅 길이 {hook_syl}음절 (규격 {HOOK_RANGE[0]}~{HOOK_RANGE[1]})")
    if hook and not hook_noun:
        flags.append("훅이 문장으로 끝남 ('~라는 ○○ 이유/근황' 같은 명사로 끝내기)")
    texts = narration_texts(sc)
    total = sum(count_syllables(t) for t in texts)
    checks["syllables"] = {"total": total, "range": list(SYLLABLE_RANGE), "ok": SYLLABLE_RANGE[0] <= total <= SYLLABLE_RANGE[1]}
    if not checks["syllables"]["ok"]:
        flags.append(f"전체 음절 {total} (규격 {SYLLABLE_RANGE[0]}~{SYLLABLE_RANGE[1]})")
    missing_hint = [i for i, s in enumerate(kept) if not (s.get("image_hint") or "").strip()]
    if missing_hint:
        flags.append(f"image_hint 없는 문장: {missing_hint}")
    if not (sc.get("closing") or "").strip():
        flags.append("끝 한 줄 없음")
    long_sents = [i + 1 for i, s in enumerate(kept) if count_syllables(s.get("text") or "") > SENTENCE_MAX_SYLLABLES]
    if long_sents:
        flags.append(f"{SENTENCE_MAX_SYLLABLES}음절이 넘는 긴 문장 (문장 {long_sents}) — 숨이 차니 두 문장으로(F6)")
    if deleted:
        flags.append(f"근거 문제로 삭제한 문장 {len(deleted)}개")
    # F9: 우리 해석 문장 (경고만 — 막지 않는다)
    checks["take"], take_flags = check_take(kept, haystack, take_deleted)
    flags += take_flags
    # F3·F4: 말투 숫자 (경고만)
    checks["style"], style_flags = check_style([s.get("text") or "" for s in kept] + [sc.get("closing") or ""])
    flags += style_flags
    # F10: 따옴표 인용은 원문 그대로 (경고만)
    checks["quotes"], quote_flags = check_quotes([s.get("text") or "" for s in kept], norm_src)
    flags += quote_flags

    sc["flags"] = list(sc.get("flags") or []) + flags
    check = {
        "ok": not flags and not deleted,
        "deleted": deleted,
        "flags": flags,
        "warnings": warnings,
        "checks": checks,
        "per_sentence_syllables": [count_syllables(t) for t in texts],
    }
    return sc, check


def save(script: dict, check: dict, out_dir: str | Path) -> tuple[Path, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    a, b = out / "script.json", out / "script_check.json"
    a.write_text(json.dumps(script, ensure_ascii=False, indent=2), encoding="utf-8")
    b.write_text(json.dumps(check, ensure_ascii=False, indent=2), encoding="utf-8")
    return a, b
