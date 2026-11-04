"""뉴스: 사용자가 직접 적은 목록(--news-json)만 쓴다. 뉴스 사이트 페이지는 절대 열지 않는다(requests·Playwright 모두).
네이버 검색 API 는 쓰지 않는다(키를 받는 곳도, 부르는 곳도 없음)."""
from __future__ import annotations

import html
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parents[2]
OUTLETS_PATH = REPO_ROOT / "config" / "outlets.json"


@dataclass
class NewsItem:
    title: str
    description: str
    originallink: str
    link: str
    pub_date: datetime  # aware
    outlet: str


def clean_text(s: str) -> str:
    s = re.sub(r"<[^>]+>", "", s or "")
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def load_outlets(path: Path | str = OUTLETS_PATH) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8")).get("domains", {})


def outlet_for(url: str, outlets: dict | None = None, *, fallback: str = "") -> str:
    outlets = load_outlets() if outlets is None else outlets
    host = urlparse(url).netloc.lower().split(":")[0]
    host = re.sub(r"^(?:(?:www|m|mobile)\.)+", "", host)
    for dom in sorted(outlets, key=len, reverse=True):
        if host == dom or host.endswith("." + dom):
            return outlets[dom]
    return fallback or host


def title_and_outlet(title: str, url: str, outlets: dict | None = None, *, outlet: str = "") -> tuple[str, str]:
    """마지막 언론사 접미사만 분리한다. 등록 이름 또는 명시한 이름과 다르면 제목을 보존한다."""
    suffix = re.fullmatch(r"(.+)\s+(?:[-|]\s+([^|:\r\n]+)|::\s*([^|:\r\n]+?)\s*::)\s*", title)
    name = (suffix[2] or suffix[3]).strip() if suffix else ""
    if not 2 <= len(name) <= 20:
        name = ""
    resolved = outlet or outlet_for(url, outlets, fallback=name)
    if name and name == resolved:
        title = suffix[1].rstrip()
    return title, resolved


def items_from_json(path: Path | str, outlets: dict | None = None) -> list[NewsItem]:
    """키 없이 쓰기: 사용자가 직접 적은 [{title, outlet, date, url, description?}] 목록."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    out = []
    for r in raw:
        d = datetime.fromisoformat(r["date"]) if r.get("date") else datetime.now(timezone.utc)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        url = r.get("url", "")
        title, outlet = title_and_outlet(r["title"], url, outlets, outlet=r.get("outlet") or "")
        out.append(NewsItem(title=title, description=r.get("description", ""), originallink=url, link=url,
                            pub_date=d, outlet=outlet))
    return out


_QUOTES = "\"'“”‘’«»「」『』`´"


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    s = "".join(ch for ch in s if ch not in _QUOTES)
    return re.sub(r"\s+", "", s)


def find_quote(item: NewsItem, text: str) -> bool:
    """대본 문장이 기사의 제목+스니펫에 그대로(공백·따옴표 정규화 후) 있는가.

    기사 본문은 열지 않으므로(크롤링 금지) 제목과 API가 준 스니펫(description)에 있는 것만 근거가 된다.
    """
    q = _norm(text)
    return bool(q) and q in _norm(item.title + " " + item.description)
