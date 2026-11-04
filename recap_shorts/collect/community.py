"""커뮤니티 글(사용자가 준 주소) 본문을 Markdown으로. Crawl4AI(Windows 는 설치된 Edge, 리눅스는 Playwright Chromium) -> 안 되면 trafilatura+Playwright 렌더.

작성자·닉네임으로 보이는 줄은 지우지 않고 그대로 둔다. 결과 파일은 work/ 아래에만 쓴다.
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path

from .http import USER_AGENT, Blocked, BlockedError, HttpClient


@dataclass
class CommunityText:
    path: Path
    markdown: str
    engine: str  # crawl4ai | trafilatura+playwright


def _crawl4ai_markdown(url: str, base_dir: Path) -> str:
    os.environ.setdefault("CRAWL4AI_NO_CLOUD_NOTICE", "1")
    os.environ.setdefault("CRAWL4_AI_BASE_DIRECTORY", str(base_dir))  # 캐시·DB도 work/ 아래
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig

    async def run():
        # 기본 UA가 가짜 브라우저 UA라서 정직한 UA로 덮어씀. Windows 에서만 설치된 Edge 를 쓰고, 리눅스는 기본 Chromium 을 쓴다.
        edge_kw = {"chrome_channel": "msedge", "channel": "msedge"} if os.name == "nt" else {}
        bc = BrowserConfig(headless=True, verbose=False, user_agent=USER_AGENT + " browser", **edge_kw)
        rc = CrawlerRunConfig(cache_mode=CacheMode.BYPASS, verbose=False)
        async with AsyncWebCrawler(config=bc) as c:
            r = await c.arun(url, config=rc)
        if not r.success:
            raise RuntimeError(r.error_message or "crawl4ai 실패")
        return str(r.markdown or "")

    return asyncio.run(run())


def _fallback_markdown(url: str) -> str:
    import trafilatura
    from .browser import edge_browser, new_context
    with edge_browser() as br:
        ctx = new_context(br, mobile=False)
        try:
            page = ctx.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
            try:
                page.wait_for_load_state("networkidle", timeout=6000)
            except Exception:
                pass
            html = page.content()
        finally:
            ctx.close()
    return trafilatura.extract(html, url=url, output_format="markdown", include_comments=True,
                               favor_recall=True) or ""


def community_text(url: str, out_md: Path | str, client: HttpClient | None = None,
                   engine: str = "auto") -> CommunityText:
    """robots 확인·3초 간격 후 본문을 Markdown 파일로 저장. engine: auto | crawl4ai | fallback"""
    out_md = Path(out_md)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    if client is not None:
        if not client.robots_allowed(url):
            raise BlockedError(Blocked(url, "robots"))
        client.wait_turn(url)
    md, used = "", ""
    if engine in ("auto", "crawl4ai"):
        try:
            md, used = _crawl4ai_markdown(url, out_md.parent / ".crawl4ai"), "crawl4ai"
        except Exception:
            if engine == "crawl4ai":
                raise
    if not md.strip() and engine in ("auto", "fallback"):
        md, used = _fallback_markdown(url), "trafilatura+playwright"
    out_md.write_text(md, encoding="utf-8")
    return CommunityText(out_md, md, used)
