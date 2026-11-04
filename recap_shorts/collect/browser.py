"""Playwright 브라우저 실행 도우미. 브라우저를 새로 내려받지 않는다.

순서: 설치된 Edge 채널 → (Windows 면) Edge 실행 파일 → Playwright 기본 Chromium.
리눅스는 Edge 가 없으면 Playwright Chromium(`python -m playwright install chromium`)을 쓴다.
셋 다 안 되면 설치 안내 문구로 RuntimeError 를 올린다.
"""
from __future__ import annotations

import os
from contextlib import contextmanager

from .http import USER_AGENT

EDGE_PATH = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
IS_WINDOWS = os.name == "nt"
NO_BROWSER_MSG = ("브라우저를 띄우지 못했습니다 — Edge 를 설치하거나 "
                  "`python -m playwright install chromium` 을 실행하세요")


@contextmanager
def edge_browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="msedge", headless=True)
        except Exception:
            try:
                if not IS_WINDOWS:  # Edge 실행 파일은 Windows 에만 있다. 리눅스는 이 단계를 건너뛴다.
                    raise RuntimeError("Edge 실행 파일이 없는 시스템")
                browser = p.chromium.launch(executable_path=EDGE_PATH, headless=True)
            except Exception:
                try:
                    browser = p.chromium.launch(headless=True)
                except Exception as e:
                    raise RuntimeError(NO_BROWSER_MSG) from e
        try:
            yield browser
        finally:
            browser.close()


def new_context(browser, *, mobile: bool = True):
    kw = dict(user_agent=USER_AGENT + " browser", locale="ko-KR")
    if mobile:
        kw.update(viewport={"width": 430, "height": 932}, device_scale_factor=3,
                  is_mobile=True, has_touch=True)
    return browser.new_context(**kw)
