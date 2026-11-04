"""브라우저 실행 순서 시험. 실제 브라우저는 띄우지 않고 가짜 playwright 를 끼운다."""
import sys
import types
from contextlib import nullcontext

import pytest

from recap_shorts.collect import browser as B

GUIDE = "브라우저를 띄우지 못했습니다 — Edge 를 설치하거나 `python -m playwright install chromium` 을 실행하세요"


class FakeBrowser:
    def __init__(self, via):
        self.via = via
        self.closed = False

    def close(self):
        self.closed = True


class FakeChromium:
    """fail 에 든 단계는 Playwright 처럼 예외를 던진다. 단계: channel(Edge 채널) · path(Edge 실행 파일) · default(기본 Chromium)."""

    def __init__(self, fail=()):
        self.fail = set(fail)
        self.tried = []

    def launch(self, **kw):
        step = "channel" if "channel" in kw else "path" if "executable_path" in kw else "default"
        self.tried.append(step)
        if step in self.fail:
            raise RuntimeError(f"{step} 단계 실패")
        return FakeBrowser(step)


def _fake_playwright(monkeypatch, *, windows, fail=()):
    chromium = FakeChromium(fail)
    mod = types.ModuleType("playwright.sync_api")
    mod.sync_playwright = lambda: nullcontext(types.SimpleNamespace(chromium=chromium))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", mod)
    monkeypatch.setattr(B, "IS_WINDOWS", windows, raising=False)
    return chromium


def test_windows_uses_edge_channel_first(monkeypatch):
    fake = _fake_playwright(monkeypatch, windows=True)
    with B.edge_browser() as br:
        assert br.via == "channel"
    assert fake.tried == ["channel"]


def test_windows_falls_back_to_edge_path(monkeypatch):
    fake = _fake_playwright(monkeypatch, windows=True, fail=("channel",))
    with B.edge_browser() as br:
        assert br.via == "path"
    assert fake.tried == ["channel", "path"]


def test_windows_falls_back_to_default_chromium(monkeypatch):
    fake = _fake_playwright(monkeypatch, windows=True, fail=("channel", "path"))
    with B.edge_browser() as br:
        assert br.via == "default"
    assert fake.tried == ["channel", "path", "default"]


def test_linux_skips_edge_path_and_uses_default_chromium(monkeypatch):
    fake = _fake_playwright(monkeypatch, windows=False, fail=("channel",))
    with B.edge_browser() as br:
        assert br.via == "default"
    assert fake.tried == ["channel", "default"]


@pytest.mark.parametrize("windows, tried", [
    (True, ["channel", "path", "default"]),
    (False, ["channel", "default"]),
])
def test_all_fail_raises_guidance(monkeypatch, windows, tried):
    fake = _fake_playwright(monkeypatch, windows=windows, fail=("channel", "path", "default"))
    with pytest.raises(RuntimeError) as exc:
        with B.edge_browser():
            pass
    assert str(exc.value) == GUIDE
    assert fake.tried == tried


def test_browser_closed_after_use(monkeypatch):
    _fake_playwright(monkeypatch, windows=False)
    with B.edge_browser() as br:
        pass
    assert br.closed
