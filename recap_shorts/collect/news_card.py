"""API에서 받은 제목·언론사·날짜로 우리가 직접 그리는 뉴스 카드 PNG (1080x810 = 영상 그림 칸, 4:3).
언론사 로고·기사 화면은 쓰지 않는다. 글꼴은 render.layout 의 Pretendard, 글꼴에 없는 글자는 바꾸거나 뺀다."""
from __future__ import annotations

import unicodedata
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from recap_shorts.render import layout

FontNotFound = layout.FontNotFoundError   # collect/__main__.py 가 이 이름으로 잡는다

W, H = 1080, 810
CARD_W, CARD_H, CARD_R = 980, 640, 36
CARD_X, CARD_Y = (W - CARD_W) // 2, (H - CARD_H) // 2      # 가운데
PAD = 56                                                    # 카드 안쪽 여백
TEXT_W = CARD_W - 2 * PAD
BG_TOP, BG_BOTTOM = (28, 30, 36), (41, 43, 49)
SHADOW_DY, SHADOW_BLUR, SHADOW_A = 12, 16, 0.6
WHITE, INK, OUTLET_INK, SUB, DIVIDER, OK_GREEN = (255, 255, 255), (17, 17, 17), (29, 29, 31), (110, 110, 115), (15, 16, 19), (31, 138, 59)
CHIP_BG, CHIP_W, CHIP_H, CHIP_R = (255, 212, 0), 113, 57, 14
HEAD_SIZE, HEAD_PITCH, HEAD_LINES = 62, 88, 3
OUTLET_SIZE, DATE_SIZE, CHIP_SIZE, FOOT_SIZE = 34, 32, 30, 30
# 카드 안 좌표(시안 news_card_1080x810.png 를 재어 맞춤). 기준선(baseline)을 기준으로 쓴다.
OUTLET_X, OUTLET_BASE, DATE_BASE = PAD + CHIP_W + 23, 96, 96
HEAD_BASE, DIVIDER_Y, FOOT_BASE = 235, 524, 585
DATE_GAP = 18

_NOTDEF = "\U0010FFFD"                    # 글꼴에 없는 글자는 모두 이 칸과 같은 빈 상자(tofu)가 된다
_LOOKALIKE = {"〇": "○"}                  # Pretendard 에 없는 글자 → 비슷한 글자


def draw_card(title: str, outlet: str, date: str | None, out: Path) -> Path:
    """뉴스 칩·언론사·(날짜)·제목(최대 3줄)·구분선·근거 문구를 넣은 카드 PNG. date 는 2026-11-02 나 2026.11.02 꼴."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    f_head, f_outlet, f_chip = layout.load_font(HEAD_SIZE), layout.load_font(OUTLET_SIZE), layout.load_font(CHIP_SIZE)
    f_foot, f_reg = layout.load_font(FOOT_SIZE), _regular(DATE_SIZE)
    card = Image.new("RGB", (CARD_W, CARD_H), WHITE)
    d = ImageDraw.Draw(card)
    _paste_rounded(card, (PAD, PAD, PAD + CHIP_W, PAD + CHIP_H), CHIP_R, CHIP_BG)
    d.text((PAD + CHIP_W // 2, PAD + CHIP_H // 2), "뉴스", font=f_chip, fill=INK, anchor="mm")
    day = (date or "").strip().replace("-", ".")
    date_s = _printable("· " + day, f_reg) if day else ""
    room = CARD_W - PAD - OUTLET_X - (d.textlength(date_s, font=f_reg) + DATE_GAP if date_s else 0)
    name = _ellipsize(d, _printable(outlet, f_outlet), f_outlet, room)
    d.text((OUTLET_X, OUTLET_BASE), name, font=f_outlet, fill=OUTLET_INK, anchor="ls")
    if date_s:
        d.text((OUTLET_X + d.textlength(name, font=f_outlet) + DATE_GAP, DATE_BASE), date_s, font=f_reg, fill=SUB, anchor="ls")
    for k, line in enumerate(_wrap(d, _printable(title, f_head), f_head, TEXT_W, HEAD_LINES)):
        d.text((PAD, HEAD_BASE + k * HEAD_PITCH), line, font=f_head, fill=INK, anchor="ls")
    d.rectangle((PAD, DIVIDER_Y, CARD_W - PAD - 1, DIVIDER_Y + 1), fill=DIVIDER)
    d.text((PAD, FOOT_BASE), "사실 확인 근거 기사", font=f_foot, fill=OK_GREEN, anchor="ls")
    d.text((CARD_W - PAD, FOOT_BASE), "원문 링크는 설명란에", font=_regular(FOOT_SIZE), fill=SUB, anchor="rs")
    img = _backdrop()
    _shadow(img)
    img.paste(card, (CARD_X, CARD_Y), _rounded_mask(CARD_W, CARD_H, CARD_R))
    img.save(out, "PNG")
    return out


def draw_news_card(title: str, outlet: str, date, out_png: Path | str) -> Path:
    """예전 CLI 호출 모양. date 는 datetime 이어도 된다."""
    return draw_card(title, outlet, fmt_date(date) if date else None, Path(out_png))


def fmt_date(d) -> str:
    return d.strftime("%Y.%m.%d") if isinstance(d, datetime) else str(d)


def _regular(size: int) -> ImageFont.FreeTypeFont:
    """날짜·오른쪽 문구는 Regular(400). render.layout 은 굵은 글꼴만 고르므로 같은 폴더의 파일을 쓴다."""
    p = Path(layout.FONT_CANDIDATES[0]).with_name("Pretendard-Regular.otf")
    return ImageFont.truetype(str(p), size) if p.exists() else layout.load_font(size)


def _ink(font, ch: str) -> bytes:
    s = int(font.size) * 2
    im = Image.new("L", (s, s), 0)
    ImageDraw.Draw(im).text((s // 4, s // 4), ch, font=font, fill=255)
    return im.tobytes()


def _printable(text: str, font) -> str:
    """글꼴에 없는 글자는 비슷한 글자(〇→○)로 바꾸고, 그것도 없으면 뺀다. 공백은 그대로 둔다."""
    box = _ink(font, _NOTDEF)

    def has(ch: str) -> bool:
        return ch.isspace() or _ink(font, ch) != box
    out = []
    for ch in text:
        if has(ch):
            out.append(ch)
            continue
        alt = _LOOKALIKE.get(ch) or unicodedata.normalize("NFKC", ch)
        if alt != ch and all(has(c) for c in alt):
            out.append(alt)
    return "".join(out)


def _wrap(draw, text: str, font, max_w: int, max_lines: int) -> list[str]:
    lines, cur = [], ""
    for word in text.split(" "):
        trial = (cur + " " + word).strip()
        if draw.textlength(trial, font=font) <= max_w:
            cur = trial
            continue
        if cur:
            lines.append(cur)
            cur = ""
        for ch in word:  # 한 단어가 한 줄보다 길면 글자 단위로
            if draw.textlength(cur + ch, font=font) > max_w:
                lines.append(cur)
                cur = ch
            else:
                cur += ch
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        while lines[-1] and draw.textlength(lines[-1] + "…", font=font) > max_w:
            lines[-1] = lines[-1][:-1]
        lines[-1] += "…"
    return lines


def _ellipsize(draw, text: str, font, max_w: int) -> str:
    if draw.textlength(text, font=font) <= max_w:
        return text
    while text and draw.textlength(text + "…", font=font) > max_w:
        text = text[:-1]
    return text + "…"


def _rounded_mask(w: int, h: int, r: int, ss: int = 4) -> Image.Image:
    m = Image.new("L", (w * ss, h * ss), 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, w * ss - 1, h * ss - 1), radius=r * ss, fill=255)
    return m.resize((w, h), Image.Resampling.LANCZOS)


def _paste_rounded(img: Image.Image, box: tuple[int, int, int, int], r: int, color) -> None:
    x0, y0, x1, y1 = box
    img.paste(color, box, _rounded_mask(x1 - x0, y1 - y0, r))


def _backdrop() -> Image.Image:
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    for y in range(H):
        t = y / (H - 1)
        d.line((0, y, W - 1, y), fill=tuple(round(a + (b - a) * t) for a, b in zip(BG_TOP, BG_BOTTOM)))
    return img


def _shadow(img: Image.Image) -> None:
    m = Image.new("L", (W, H), 0)
    ImageDraw.Draw(m).rounded_rectangle((CARD_X, CARD_Y + SHADOW_DY, CARD_X + CARD_W - 1, CARD_Y + SHADOW_DY + CARD_H - 1),
                                        radius=CARD_R, fill=round(255 * SHADOW_A))
    img.paste((0, 0, 0), (0, 0, W, H), m.filter(ImageFilter.GaussianBlur(SHADOW_BLUR)))
