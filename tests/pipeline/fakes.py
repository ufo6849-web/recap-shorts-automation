"""외부 호출(디시, Gemini, 이미지 서버, 목소리, 렌더, Clef, 사실 확인, 뉴스 카드)을 전부 가짜로 바꾼 Deps."""
from __future__ import annotations

import json
import wave
from pathlib import Path

from PIL import Image, ImageDraw

from recap_shorts import factcheck as F
from recap_shorts.collect.blind import Post as BlindPost
from recap_shorts.collect.dcinside import Post
from recap_shorts.pipeline import Deps
from recap_shorts.voice.pipeline import narration_roles

READ_TEXT = "동네 축구 결승전, 구청 직원 3명이 선발로 출전\n상대 팀은 12개 동 대표 선수들, 우승 상금 30만 원"

DRAFT = {
    "title_lines": ["동네 축구 결승전", "구청 직원 셋?"],
    "hook": "동네 축구 결승에 구청 직원이 나온 이유",
    "sentences": [
        {"text": "동네 축구대회 결승에 구청 직원 세 명이 선발로 출전했대.", "evidence": "동네 축구 결승전, 구청 직원 3명이 선발로 출전",
         "source_id": "post", "emphasis": "구청", "image_hint": "구청 사무실 풍경, 사진"},
        {"text": "상대 팀은 열두 개 동 대표 선수들이었는데 말이야.", "evidence": "상대 팀은 12개 동 대표 선수들, 우승 상금 30만 원",
         "source_id": "post", "emphasis": None, "image_hint": "AI 장면: 동네 운동장 / 캡처: 비교표의 상대 팀 줄"},
        {"text": "근데 이게 전부 구청 공무원들끼리 하는 리그라니 놀랍지 않아?", "evidence": None, "source_id": None, "emphasis": None,
         "image_hint": "구청 민원실, 사진"},
        {"text": "그래도 축구는 끝까지 모르는 거잖아.", "evidence": None, "source_id": None, "emphasis": None,
         "image_hint": "동네 운동장 전경"},
    ],
    "closing": "이쯤 되면 구청 직원이 무섭다",
    "flags": [],
}

REFINED_TEXTS = [
    "동네 축구대회 결승에 나온 선발 셋이 구청 직원이었어.",
    "반면 상대 팀은 열두 개 동 대표 선수들이었잖아.",
    "근데 이게 전부 구청 공무원들끼리 하는 리그라니, 놀랍지 않아?",
    "그래도 축구는 끝까지 모르는 거지.",
]

# 사실 확인 시험용 기사·주장(네트워크 없음). 주장 근거 구절은 READ_TEXT 안에 있다.
FC_URL = "https://news.example.kr/society/2026/11/02/football-final"
FC_SOURCE = {"url": FC_URL, "title": "동네 축구 결승에 구청 직원 출전", "outlet": "조선일보", "date": "2026-11-02"}
FC_CLAIM = {"id": "C1", "claim": "구청 직원 3명이 결승에 선발로 나왔다", "query": "구청 직원 축구 결승",
            "source_quote": "구청 직원 3명이 선발로 출전"}
FC_FACT = "구청 직원 3명이 선발로 출전했다"


class FakeLLM:
    def __init__(self, kind="draft+refine", refine_texts=None, fail_refine=False, draft=None):
        self.calls, self.prompts = [], []
        self.model = "fake-model"
        self.draft = draft or DRAFT
        self.refine_texts = REFINED_TEXTS if refine_texts is None else refine_texts
        self.fail_refine = fail_refine

    def complete_json(self, prompt):
        self.prompts.append(prompt)
        self.calls.append({"seconds": 0.1, "ok": True})
        if "교정자" in prompt:
            if self.fail_refine:
                raise RuntimeError("boom")
            out = json.loads(json.dumps(self.draft))
            for s, t in zip(out["sentences"], self.refine_texts):
                s["text"] = t
            return out
        return json.loads(json.dumps(self.draft))


def png(path: Path, size=(400, 300), color=(80, 120, 200)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    im = Image.new("RGB", size, color)
    ImageDraw.Draw(im).rectangle((10, 10, size[0] - 10, size[1] - 10), outline=(255, 255, 255), width=4)
    im.save(path)


class FakeDeps(Deps):
    def __init__(self, tall: bool = True, llm: FakeLLM | None = None, image_up: bool = True):
        self.tall, self.llm, self.image_up = tall, llm or FakeLLM(), image_up
        self.calls: dict[str, int] = {}
        self.fail_once: set[str] = set()
        self.clef_matrix = None
        self.generated: list[str] = []
        self.redo_args: list = []
        # 사실 확인: 기본은 일반 글(건너뜀). 시험이 kind·판정·실패를 바꾼다.
        self.fc_kind = "general"                               # 분류 결과 kind
        self.fc_claims: list[dict] = [dict(FC_CLAIM)]          # 분류가 뽑은 주장
        self.fc_verdicts: dict[str, dict] = {}                 # 주장 id → 판정 항목(빠진 칸은 "보도로 확인됨")
        self.fc_classify_error: str | None = None              # 분류에서 낼 FactcheckError 문장
        self.fc_verify_error: str | None = None                # 판정에서 낼 FactcheckError 문장
        self.fc_script_error: str | None = None                # 대본 검수에서 낼 FactcheckError 문장
        self.fc_script_flags: list[dict] = []                  # 대본 검수 결과(script_check 모양)
        self.fc_hook = None                                    # 분류 때 한 번 부르는 함수(시험이 낀 순간을 만든다)
        self.fc_draw_fail: set[str] = set()                    # 이 제목의 뉴스 카드는 그리기에 실패한다
        self.news_cards: list[dict] = []                       # 그린 뉴스 카드 기록

    def hit(self, name: str) -> None:
        self.calls[name] = self.calls.get(name, 0) + 1
        if name in self.fail_once:
            self.fail_once.discard(name)
            raise RuntimeError(f"{name} 일부러 실패")

    # 디시
    def http_client(self, job_dir):
        return object()

    def get_post(self, client, gallery, no, minor, site="dcinside"):
        self.hit("get_post")
        if site == "blind":
            return BlindPost(gallery_id=gallery, no=no, url=f"https://www.teamblind.com/kr/post/{no}",
                             title="점심값이 올라서 슬픈 사람의 이야기", text="회사 근처 식당이 다 비싸졌다.\n그래도 한 끼는 먹어야 한다.",
                             written_at="2026-10-31 23:00", views=1234, recommends=12,
                             images=["https://img/blind1", "https://img/blind2"], topic="블라블라")
        return Post(gallery_id=gallery, no=no, url=f"https://gall.dcinside.com/board/view/?id={gallery}&no={no}",
                    title="[가상] 동네 축구 결승전에 구청 직원이 셋이나", text="동네 축구대회 결승인데 구청 직원이 셋이나 나왔네...\n대단하다!! 병신들",
                    written_at="2026-09-26 01:55:37", views=22740, recommends=507,
                    images=["https://img/1", "https://img/2"])

    def download_image(self, client, url, dest_noext, referer):
        self.hit("download_image")
        p = dest_noext.with_suffix(".png")
        if url.endswith("2") and self.tall:
            p.parent.mkdir(parents=True, exist_ok=True)
            im = Image.new("RGB", (600, 2000), (240, 240, 240))
            d = ImageDraw.Draw(im)
            for y in (100, 400, 1000, 1500):
                d.rectangle((40, y, 560, y + 300), fill=(30, 30, 30))
            im.save(p)
        else:
            png(p)
        return p

    def screenshot_post(self, client, url, out_png, site="dcinside"):
        self.hit("screenshot_post")
        png(out_png, (800, 1200), (255, 255, 255))
        im = Image.open(out_png)
        d = ImageDraw.Draw(im)
        d.rectangle((10, 10, 300, 30), fill=(20, 20, 20))          # 글 머리: 제목(왼쪽)
        d.rectangle((600, 35, 780, 50), fill=(20, 20, 20))         # 조회·추천(오른쪽)
        im.save(out_png)
        png(Path(out_png).with_name("comments.png"), (800, 1800), (250, 250, 250))

        class Shot:
            needs_manual_blur = False
            parts = {"head": [0, 0, 800, 60], "comments_file": "comments.png"}
        return Shot()

    # Gemini
    def read_image(self, images, instruction):
        self.hit("read_image")
        if "댓글 목록" in instruction:
            return {"comments": ["이건 좀 웃기네", "우리 동네도 비슷한 일 있었어", "ㅋ"]}
        return {"kind": "비교표", "fan_made": True, "text": READ_TEXT, "summary": "표", "has_face": False,
                "has_id_text": False}

    def script_llm(self, kind=None, model=None):
        return self.llm

    def hint_prompts(self, hints):
        self.hit("hint_prompts")
        return [f"scene {i}" for i, _ in enumerate(hints)]

    def locate_regions(self, path, wants):
        """첫 번째 찾을 부분만 찾았다고 하고, 세로로 긴 그림(첨부 2)에서는 가릴 곳을 못 찾았다고 한다."""
        self.hit("locate_regions")
        with Image.open(path) as im:
            w, h = im.size
        parts = [(20, 40, min(w, 380), 90) if k == 0 else None for k in range(len(wants))]
        return {"parts": parts, "private": [] if h > 1.5 * w else [(5, 5, 40, 40)], "size": [w, h]}

    # 이미지 서버
    def image_available(self):
        return self.image_up

    def generate_image(self, prompt, out_png):
        self.hit("generate_image")
        self.generated.append(prompt)
        png(Path(out_png), (1448, 1086), (20, 160, 90))
        return {"ok": True, "file": str(out_png), "seconds": 0.0, "error": None}

    # 목소리·렌더
    def make_voice(self, script, out_dir, engine, check_asr):
        self.hit("make_voice")
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        texts, roles = narration_roles(script)
        sents, t = [], 0.1
        for i, (tx, ro) in enumerate(zip(texts, roles)):
            sents.append({"index": i, "role": ro, "text": tx, "spoken": tx, "start": round(t, 3), "end": round(t + 3.0, 3),
                          "syllables": len(tx), "heard": tx, "match": 1.0})
            t += 3.1
        tm = {"voice": "voice.wav", "sample_rate": 8000, "duration": round(t + 0.2, 3), "speech_start": 0.1,
              "speech_end": round(t - 0.1, 3), "syllables": 200, "rate": 6.5, "sentences": sents,
              "engine": engine, "asr_mean_match": 1.0}
        (out / "timings.json").write_text(json.dumps(tm, ensure_ascii=False), encoding="utf-8")
        with wave.open(str(out / "voice.wav"), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(8000)
            w.writeframes(b"\x00\x00" * 800)
        return tm

    def redo_voice(self, voice_dir, index, variants):
        self.hit("redo_voice")
        self.redo_args.append((index, variants))
        return {"trials": [], "chosen": {"variant": 0, "match": 0.97}, "timings": {}}

    def render(self, timeline, out_mp4, progress=None):
        self.hit("render")
        if progress:
            progress(0.5)
            progress(1.0)
        Path(out_mp4).write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"0" * 64)
        return {"out": str(out_mp4), "duration": 30.0}

    # 사실 확인(네트워크 없음)
    def factcheck_classify(self, title, text, force=False):
        self.hit("factcheck_classify")
        if self.fc_hook:
            self.fc_hook()
        if self.fc_classify_error:
            raise F.FactcheckError(self.fc_classify_error)
        claims = [dict(c) for c in self.fc_claims] if (force or self.fc_kind != "general") else []
        return {"kind": self.fc_kind, "reason": "시험용 분류", "claims": claims}

    def factcheck_verify(self, claims, *, title="", source_text=""):
        self.hit("factcheck_verify")
        if self.fc_verify_error:
            raise F.FactcheckError(self.fc_verify_error)
        results = []
        for c in claims:
            v = {"verdict": "true", "why": "보도로 확인됨", "fact": FC_FACT, "sources": [dict(FC_SOURCE)]}
            v.update(self.fc_verdicts.get(c["id"], {}))
            results.append({"id": c["id"], **v})
        return {"results": results, "overall_why": "시험용 판정", "model": "fake-verify", "search": "exa:5",
                "cost_usd": 0.02}

    def factcheck_script_check(self, script, record):
        self.hit("factcheck_script_check")
        if self.fc_script_error:
            raise F.FactcheckError(self.fc_script_error)
        return [dict(f) for f in self.fc_script_flags]

    def draw_news_card(self, title, outlet, date, out):
        self.hit("draw_news_card")
        if title in self.fc_draw_fail:
            raise RuntimeError("뉴스 카드 그리기 실패(시험)")
        self.news_cards.append({"title": title, "outlet": outlet, "date": date, "file": str(out)})
        png(Path(out), (108, 81), (240, 240, 240))
        return Path(out)

    # Clef
    def clef_inspect(self, path, kind):
        return None

    def clef_fit_matrix(self, sentences, images):
        return self.clef_matrix(sentences, images) if self.clef_matrix else None

    def clef_watch(self, mp4, topic_line):
        return None

    def clef_triage(self, posts):
        return None
