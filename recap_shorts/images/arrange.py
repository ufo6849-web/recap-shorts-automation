"""장면 자동 배치: 문장(타이밍 index)마다 보여 줄 그림을 고른다.

'캡처 위주' 배치 — 화면은 글·댓글 캡처를 주로 쓰고, AI 그림은 캡처가 없는 자리를 채우는 보조로 쓴다.
 - 캡처가 먼저(1단계), AI 는 남는 자리에 문장당 1장까지 맨 끝에 보조로(2단계, AI 컷 비율 상한 max_ai_share 기본 0.34).
 - 같은 그림을 두 번 쓰지 않는다: 맞는 그림이 없으면 칸을 비우고(컷이 길어짐), 문장이 통째로 비면 그때만 다시 쓴다.
 - 점수표 scores[i][j] = 문장 i 에 그림 j 가 어울리는 정도(0~1).
   Clef 점수(recap_shorts.clef.match)가 있으면 0.5*Clef + 0.5*규칙 점수, 없으면 규칙 점수만.
 - 규칙 점수: 이 문장을 위해 잘라 낸 캡처(focus) +0.75, 디시 글 머리 카드는 훅에 +0.75,
   통째 캡처는 화면 설명이 캡처를 말하면 +0.2 와 글 순서가 비슷할수록 +0.15, 그 문장용 AI 그림 +0.45.
 - 대본이 다루지 않는 이야기의 첨부는 통째 캡처 점수 0(자동 배치에서 빠지고 편집기 후보에만 남음).
   한 글에 여러 이야기(블라인드 글 두 개·채용 공고 넷 등)가 묶여 있고 대본은 그중 하나만 다루면,
   나머지 캡처가 빈자리를 채워 공고에 이름이 보이는 회사 이야기처럼 보인다(off_script 참고).
   그 첨부를 말하는 문장에는 같은 첨부의 통째 캡처 +0.15.
 - 글자가 거의 없는 사진·짤(첨부 text 가 짧고 kind 에 '사진'·'짤')은 다른 이야기로 빼지 않는다.
   글쓴이 사진 8장처럼 겹쳐 볼 글자가 없는 사진은 글자로 가리면 전부 0점이 되고
   AI 가 만든 그림이 그 자리를 채워(시청자에게 글쓴이 사진처럼 보임) 버린다. 글자 많은 캡처를 가려내던 규칙은 그대로 둔다.
 - 뉴스 카드(role "news"): 사실과 evidence 가 맞으면 NEWS_MATCH, 아니면 사실·제목과 문장의 글자 겹침으로 점수를 준다.
   훅은 0. 캡처처럼 칸을 세고 순서를 매기며, AI 로 세지 않는다.
"""
from __future__ import annotations

import re

from recap_shorts.script.text import normalize_loose

CAP_WORDS = ("캡처", "표", "카드", "스크린샷", "게시글", "글 ")
NEWS_MATCH = 0.95            # 뉴스 카드의 사실을 근거로 쓴 문장의 점수
NEWS_OVERLAP_MIN = 0.3       # 바꿔 쓴 근거도 받되 우연히 몇 글자만 겹친 문장은 제외한다.
NEWS_FALLBACK_MIN = 0.15     # 카드가 모두 빠졌을 때도 최소한의 관련성이 있는 본문에만 보정한다.
ATTACH_REF = re.compile(r"첨부\s*그림\s*(\d+)")
SAME_ATTACH_BONUS = 0.15      # 문장이 말하는 첨부의 통째 캡처
RELATED_MIN = 0.35            # 대본이 말한 첨부와 글자(두 글자씩) 겹침이 이만큼이면 같은 이야기(다른 이야기는 많아야 0.20)
PHOTO_KIND_WORDS = ("사진", "짤")   # 글자가 거의 없는 그림의 kind(글자로 이야기를 가려내면 늘 빠져 AI 가 그 자리를 채운다)
PHOTO_TEXT_MAX = 40           # 이만큼 안쪽의 글자만 있으면 사진으로 본다(글쓴이 사진은 0~16자)
CAP_BONUS = 0.25              # 같은 칸이면 캡처를 AI 보다 먼저(배치 비용에만, 보여 주는 점수에는 안 넣음)
FOREIGN_FOCUS_PENALTY = 1.0   # 다른 문장을 위해 잘라 낸 캡처를 이 문장에 쓰는 비용(디시 글 머리는 훅 것)
SKIP_COST = -0.45             # 추가 칸을 비우는 비용: 캡처는 점수 0.2 넘어야 컷을 늘림(캡처 보너스 0.25 포함)
BASIC_SKIP = -0.3             # 첫 칸을 캡처로 못 채우는 비용: 점수 0.05 넘는 캡처면 넣음(아니면 AI 로)


def shots_for(span: float, is_hook: bool, target: float = 1.4) -> int:
    if is_hook:
        return 1
    return max(1, min(3, round(span / target)))


def _att(im: dict) -> str | None:
    a = im.get("attachment")
    return None if a in (None, "") else str(a)


def sentence_attachments(sentences: list[dict], images: list[dict]) -> list[set[str]]:
    """문장마다 그 문장이 말하는 첨부 번호: 화면 설명의 "첨부 그림 N" + 그 문장용으로 잘라 낸 캡처의 첨부."""
    out = []
    for s in sentences:
        refs = set(ATTACH_REF.findall(s.get("hint") or ""))
        refs |= {_att(im) for im in images if im.get("role") == "focus" and im.get("sentence") == s["index"] and _att(im)}
        out.append(refs)
    return out


def _grams(text: str) -> set[str]:
    text = re.sub(r"\[(아이디|닉네임)\]|[^가-힣A-Za-z0-9]", " ", text or "")
    return {w[k:k + 2] for w in text.split() if len(w) >= 2 for k in range(len(w) - 1)}


def _overlap(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a) if a else 0.0


def _is_photo(a: dict) -> bool:
    """글자가 거의 없는 사진·짤(글쓴이 사진: kind "사진", text 0~16자).
    이런 그림은 글자 겹침을 잴 수 없어 다른 이야기로 빼면 늘 0점이 되고, AI 가 그 자리를 채워
    글쓴이 사진처럼 보이게 된다. 이야기를 가리는 규칙에서는 빼지 않는다(점수는 예전 규칙 그대로)."""
    kind = str(a.get("kind") or "")
    text = re.sub(r"\s", "", str(a.get("text") or ""))
    return any(w in kind for w in PHOTO_KIND_WORDS) and len(text) <= PHOTO_TEXT_MAX


def unrelated_attachments(sentences: list[dict], images: list[dict], attachments: list[dict] | None) -> set[str]:
    """대본이 다루지 않는 이야기의 첨부 번호. attachments = topic.json 의 첨부 목록({n, kind, summary, text, dc_comments}).

    첨부를 순서대로 '이야기' 로 묶는다: 댓글 캡처는 바로 앞 이야기에 붙고, 나머지는 새 이야기를 시작한다.
    대본이 말한 첨부(sentence_attachments)가 든 이야기, 또는 말한 첨부와 글자가 RELATED_MIN 이상 겹치는 첨부가
    든 이야기(같은 영상의 다른 장면, 같은 일을 다룬 다른 트윗)는 남긴다. 글자가 거의 없는 사진·짤(_is_photo)도 남긴다 —
    글자로 이야기를 가릴 수 없고, 빼면 AI 그림이 글쓴이 사진 자리를 채운다. 디시 댓글(이 글 자체의 댓글)은 늘 남긴다.
    말한 첨부를 못 찾거나 첨부 목록이 없으면 빈 집합(예전처럼 모두 씀) — 대본이 첨부 번호를 안 적는 글도 있다."""
    per = sentence_attachments(sentences, images)
    named = set().union(*per) if per else set()
    atts = sorted((a for a in attachments or [] if a.get("n") is not None and not a.get("dc_comments")),
                  key=lambda a: int(a["n"]))
    if not named or not atts:
        return set()
    def text_of(a):
        return f"{a.get('text') or ''} {a.get('summary') or ''}"
    ref_all = set().union(*(_grams(text_of(a)) for a in atts if str(a["n"]) in named))
    ref_sum = set().union(*(_grams(a.get("summary") or "") for a in atts if str(a["n"]) in named))
    stories: list[list[dict]] = []
    for a in atts:
        # 글자 없는 사진·짤은 언제나 살린다(제 이야기에 붙여서 그 이야기도 살리고, 이야기 경계는 그대로).
        if stories and "댓글" in (a.get("kind") or ""):
            stories[-1].append(a)
        else:
            stories.append([a])
    out: set[str] = set()
    for story in stories:
        keep = any(_is_photo(a) or str(a["n"]) in named
                   or max(_overlap(_grams(text_of(a)), ref_all),
                          _overlap(_grams(a.get("summary") or ""), ref_sum)) >= RELATED_MIN
                   for a in story)
        if not keep:
            out |= {str(a["n"]) for a in story}
    return out


def off_script(sentences: list[dict], images: list[dict], attachments: list[dict] | None = None) -> set[int]:
    """unrelated_attachments 에서 나온 통째 캡처의 index."""
    drop = unrelated_attachments(sentences, images, attachments)
    return {j for j, im in enumerate(images) if im["kind"] == "cap" and not im.get("role") and _att(im) in drop}


def _news_match(evidence: str | None, fact_text: str | None) -> bool:
    """문장의 evidence 와 뉴스 카드의 fact_text 가 같은 사실인가(정규화 후 같거나 한쪽이 다른 쪽을 품음). 빈 글은 맞지 않는다."""
    a, b = normalize_loose(evidence or ""), normalize_loose(fact_text or "")
    return bool(a and b) and (a == b or a in b or b in a)


def _news_fact_texts(image: dict) -> list[str]:
    return image.get("fact_texts") or [image.get("fact_text") or ""]


def news_overlap(sentence: dict, image: dict) -> float:
    """문장 text+evidence 의 두 글자 조각 중 카드의 사실·제목에도 있는 비율."""
    text = f"{sentence.get('text') or ''} {sentence.get('evidence') or ''}"
    reference = " ".join(_news_fact_texts(image) + [image.get("title") or ""])
    return _overlap(_grams(text), _grams(reference))


def heuristic_scores(sentences: list[dict], images: list[dict], attachments: list[dict] | None = None) -> list[list[float]]:
    """sentences: [{"index","hint","role"}], images: [{"file","kind","sentence","role","attachment"}]
    image role: "focus"(문장용으로 잘라 낸 캡처, sentence 있음) | "dc_head"(디시 글 머리) | "news"(뉴스 카드:
    fact_texts 와 evidence 매칭, 또는 사실·제목과 문장 겹침) | None(통째 캡처·AI).
    attachments: topic.json 의 첨부 목록(없으면 다른 이야기 첨부를 가려내지 않음)."""
    n = len(sentences)
    whole = [j for j, im in enumerate(images) if im["kind"] == "cap" and not im.get("role")]
    rank = {j: k / max(1, len(whole) - 1) for k, j in enumerate(whole)}
    per_sentence = sentence_attachments(sentences, images)
    drop = off_script(sentences, images, attachments)
    out = []
    for i, s in enumerate(sentences):
        hint = s.get("hint") or ""
        hook = s.get("role") == "hook"
        wants_cap = any(w in hint for w in CAP_WORDS)
        pos = i / max(1, n - 1)
        row = []
        for j, im in enumerate(images):
            v = 0.2
            role = im.get("role")
            if im["kind"] == "ai":
                v += 0.45 if im.get("sentence") == s["index"] else 0.0
            elif role == "focus":
                v += 0.75 if im.get("sentence") == s["index"] else 0.0
            elif role == "dc_head":
                v += 0.75 if hook else -0.1
            elif role == "news":
                v = 0.0
                if not hook:
                    if any(_news_match(s.get("evidence"), fact) for fact in _news_fact_texts(im)):
                        v = NEWS_MATCH
                    else:
                        overlap = news_overlap(s, im)
                        if overlap >= NEWS_OVERLAP_MIN:
                            v = 0.5 + 0.35 * (overlap - NEWS_OVERLAP_MIN) / (1 - NEWS_OVERLAP_MIN)
            elif j in drop:
                v = 0.0                                                # 이 영상이 말하지 않는 첨부
            else:
                v += (0.2 if wants_cap or hook else 0.05) + 0.15 * (1 - abs(rank.get(j, 0.5) - pos))
                v += SAME_ATTACH_BONUS if _att(im) in per_sentence[i] else 0.0
            row.append(max(0.0, min(1.0, v)))
        out.append(row)
    return out


def blend(heur: list[list[float]], clef: list[list[float]] | None, drop: set[int] | None = None) -> list[list[float]]:
    """drop(off_script) 은 Clef 를 섞어도 0 — Clef 는 그림이 문장과 비슷해 보이는지만 보니 남의 회사 공고도 높게 준다."""
    if not clef:
        return heur
    drop = drop or set()
    return [[0.0 if j in drop else 0.5 * c + 0.5 * h for j, (c, h) in enumerate(zip(crow, hrow))]
            for crow, hrow in zip(clef, heur)]


def plan_with_ai_cap(scores: list[list[float]], counts: list[int], kinds: list[str],
                     owners: list[int | None] | None, share: float) -> list[list[int]]:
    return assign(scores, counts, kinds, owners, ai_share=share)


def _hungarian(cmat: list[list[float]]) -> dict[int, int]:
    """행 ≤ 열인 비용표의 최소 비용 배정 {행: 열}. scipy 가 있으면 그것을, 없으면 같은 답을 내는 순수 파이썬
    (포텐셜을 쓰는 헝가리안, O(n²m)). scipy 가 없는 환경에서는 예전 욕심쟁이 대체가
    돌고 있었고, 그 때문에 문장이 비어 같은 그림을 다시 쓰는 일이 생겼다(tests/pipeline/test_units.py 두 개 실패)."""
    try:
        from scipy.optimize import linear_sum_assignment
    except ImportError:
        linear_sum_assignment = None
    if linear_sum_assignment is not None:
        rows, cidx = linear_sum_assignment(cmat)
        return {int(r): int(c) for r, c in zip(rows, cidx)}
    n = len(cmat)
    if n == 0:
        return {}
    m = len(cmat[0])
    if n > m:
        raise ValueError("행이 열보다 많습니다")
    inf = float("inf")
    u, v = [0.0] * (n + 1), [0.0] * (m + 1)
    p, way = [0] * (m + 1), [0] * (m + 1)       # p[열] = 그 열에 배정된 행(1부터), 0 은 비어 있음
    for i in range(1, n + 1):
        p[0], j0 = i, 0
        minv, used = [inf] * (m + 1), [False] * (m + 1)
        while True:
            used[j0] = True
            i0, delta, j1 = p[j0], inf, 0
            for j in range(1, m + 1):
                if not used[j]:
                    cur = cmat[i0 - 1][j - 1] - u[i0] - v[j]
                    if cur < minv[j]:
                        minv[j], way[j] = cur, j0
                    if minv[j] < delta:
                        delta, j1 = minv[j], j
            for j in range(m + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while j0:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
    return {p[j] - 1: j - 1 for j in range(1, m + 1) if p[j]}


def assign(scores: list[list[float]], counts: list[int], kinds: list[str] | None = None,
           owners: list[int | None] | None = None, ai_share: float = 1.0) -> list[list[int]]:
    """문장별 그림 index 목록(캡처 먼저, AI 는 맨 끝에 1장까지). kinds[j] = kind_of(그림) (없으면 모두 cap 취급).
    owners[j] = 그 그림을 잘라 낸 문장의 위치(문장 순서 번호) — 다른 문장에는 되도록 안 씀.

    1단계(캡처): 문장마다 counts 칸까지 캡처를 헝가리안으로 놓는다. 맞는 캡처가 없으면 칸을 비운다
       (첫 칸은 BASIC_SKIP, 나머지는 SKIP_COST 보다 나을 때만) — 억지로 엉뚱한 캡처를 끌어오지 않게.
    2단계(AI 보조): 자리가 남은 문장에 AI 1장을 맨 끝에 붙인다. 한 컷이 긴 문장부터, AI 컷이 전체의
       ai_share 를 넘지 않을 때까지. 캡처가 하나도 못 들어간 문장은 비율과 상관없이 AI(없으면 캡처 재사용)."""
    n_img = len(scores[0]) if scores else 0
    if n_img == 0:
        raise ValueError("배치할 그림이 없습니다")
    kinds = kinds or ["cap"] * n_img
    caps = [j for j in range(n_img) if kinds[j] != "ai"]
    ais = [j for j in range(n_img) if kinds[j] == "ai"]

    def foreign(i, j):
        return FOREIGN_FOCUS_PENALTY if owners and owners[j] is not None and owners[j] != i else 0.0
    plan: list[list[int]] = [[] for _ in counts]
    if caps:
        slots = [(i, k == 0) for i, c in enumerate(counts) for k in range(c)]
        cols = [(j, 0) for j in caps] + [(-1, 0)] * len(slots)
        cmat = [[(BASIC_SKIP if basic else SKIP_COST) if j < 0 else -scores[i][j] - CAP_BONUS + foreign(i, j)
                 for j, _ in cols] for i, basic in slots]
        pick = _hungarian(cmat)
        for r, (i, _) in enumerate(slots):
            j = cols[pick[r]][0]
            if j >= 0:
                plan[i].append(j)
    # 2단계: AI 보조
    used_ai: set[int] = set()

    def best_ai(i):
        free = [j for j in ais if j not in used_ai]
        return max(free, key=lambda j: scores[i][j] - foreign(i, j)) if free else None
    for i in range(len(counts)):                      # 캡처가 없는 문장은 꼭
        if not plan[i] and (j := best_ai(i)) is not None:
            plan[i].append(j)
            used_ai.add(j)
    order = sorted((i for i in range(len(counts)) if 0 < len(plan[i]) < counts[i] and not any(kinds[j] == "ai" for j in plan[i])),
                   key=lambda i: len(plan[i]) / counts[i])
    for i in order:
        total = sum(len(p) for p in plan)
        n_ai = sum(1 for p in plan for j in p if kinds[j] == "ai")
        if n_ai + 1 > ai_share * (total + 1):
            break
        j = best_ai(i)
        if j is None:
            break
        plan[i].append(j)
        used_ai.add(j)
    for i in range(len(counts)):                      # 그래도 빈 문장: 가장 맞는 그림을 다시 씀
        if not plan[i]:
            plan[i].append(max(range(n_img), key=lambda j: scores[i][j] - foreign(i, j)))
    for i, lst in enumerate(plan):   # 한 문장 안: 통째 캡처(전체) → 잘라 낸 캡처(확대) → AI
        lst.sort(key=lambda j: (kinds[j] == "ai", kinds[j] == "focus", -scores[i][j]))
    return plan


def kind_of(image: dict) -> str:
    """assign 의 kinds 값: "ai" | "focus" | "dc_head" | "news" | "cap"(통째 캡처). news 는 assign 에서 cap 처럼 센다."""
    return "ai" if image["kind"] == "ai" else (image.get("role") or "cap")
