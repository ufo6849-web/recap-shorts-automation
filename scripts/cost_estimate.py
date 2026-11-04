"""영상 1편의 외부 호출 비용과 '하루 n편 × 30일' 월 비용을 계산한다.

    <파이썬> scripts/cost_estimate.py --since 2026-01-01T00:00:00 [--until …] [--per-day 3] [--days 30] [--log 파일]

입력: work/_usage.jsonl (recap_shorts/usage.py 가 호출마다 남기는 기록), config/prices.json (단가와 출처).
since/until 은 UTC ISO 시각(기록의 ts 와 같은 형식). 그 구간을 '영상 1편'으로 본다.
Gemini TTS 는 응답에 토큰 수가 없으면 소리 길이(초) × 초당 25 토큰으로 계산한다.
Clef 는 하루 무료 1만 뉴런을 뺀 나머지만 돈이 든다(넘기려면 Workers Paid 요금제).
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from recap_shorts import usage  # noqa: E402


def per_video(rows: list[dict], prices: dict, after: bool = False) -> dict:
    out = defaultdict(lambda: {"calls": 0, "usd": 0.0})
    neurons = 0.0
    images = 0
    for r in rows:
        k = r.get("kind")
        if k == "gemini_text":
            p = prices["gemini_text"].get(r.get("model"), {})
            if after and p.get("after"):
                p = {**p, **p["after"]}
            inp = r.get("prompt_tokens") or 0
            outp = (r.get("output_tokens") or 0) + (r.get("thought_tokens") or 0)
            usd = inp / 1e6 * p.get("input_per_m", 0) + outp / 1e6 * p.get("output_per_m", 0)
            key = f"Gemini 글·그림 읽기 ({r.get('model')})"
        elif k == "gemini_tts":
            p = prices["gemini_tts"].get(r.get("model"), {})
            if after and p.get("after"):
                p = {**p, **p["after"]}
            u = r.get("usage") or {}
            out_tok = u.get("output_tokens") or u.get("candidatesTokenCount") or (r.get("audio_sec") or 0) * p.get("audio_tokens_per_sec", 25)
            in_tok = u.get("input_tokens") or u.get("promptTokenCount") or (r.get("chars") or 0) * 1.0
            usd = in_tok / 1e6 * p.get("input_per_m", 0) + out_tok / 1e6 * p.get("audio_out_per_m", 0)
            key = f"Gemini 목소리 ({r.get('model')})"
        elif k == "openrouter":                      # 응답에 실제로 찍힌 비용(cost)을 그대로 더한다
            usd = float(r.get("cost") or 0)
            key = f"OpenRouter ({r.get('model')})"
        elif k == "clef":
            neurons += r.get("neurons") or 0
            out["Clef 판단 (Cloudflare)"]["calls"] += 1
            continue
        elif k == "image":
            images += 1
            model = r.get("model") or "local-image-server"
            if model in prices["image"]:            # Gemini 이미지(유료): 장당 값
                key = f"AI 이미지 ({model})"
                usd = prices["image"][model]["per_image"]
            else:                                    # 로컬 이미지 서버(한도형): 추가 비용 없음
                out["AI 이미지 (로컬 이미지 서버·한도형)"]["calls"] += 1
                continue
        else:
            continue
        out[key]["calls"] += 1
        out[key]["usd"] += usd
    return {"items": dict(out), "neurons": neurons, "images": images}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", required=True)
    ap.add_argument("--until")
    ap.add_argument("--per-day", type=int, default=3)
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--log", help="기록 파일(기본 work/_usage.jsonl 또는 RECAP_USAGE_LOG)")
    a = ap.parse_args()
    prices = json.loads((ROOT / "config" / "prices.json").read_text(encoding="utf-8"))
    rows = usage.read(a.since, a.until, Path(a.log) if a.log else None)
    res = {}
    for label, after in (("지금 단가", False), ("할인 끝난 뒤 단가", True)):
        v = per_video(rows, prices, after)
        gem = sum(x["usd"] for x in v["items"].values())
        day_neurons = v["neurons"] * a.per_day
        cf = prices["cloudflare"]
        paid_neurons = max(0.0, day_neurons - cf["free_neurons_per_day"])
        clef_month = paid_neurons / 1000 * cf["per_1k_neurons"] * a.days
        month = {"gemini": round(gem * a.per_day * a.days, 2), "clef_usage": round(clef_month, 2),
                 "clef_plan": cf["workers_paid_plan_per_month"] if paid_neurons > 0 else 0.0,
                 "images_api_medium": round(v["images"] * a.per_day * a.days * prices["image"]["api_gpt_image_2_medium"]["per_image"], 2)}
        res[label] = {"per_video": {k: {"calls": x["calls"], "usd": round(x["usd"], 4)} for k, x in v["items"].items()},
                      "per_video_gemini_usd": round(gem, 4), "per_video_neurons": round(v["neurons"]),
                      "per_video_images": v["images"], "month": month,
                      "month_total_subscription_images": round(month["gemini"] + month["clef_usage"] + month["clef_plan"], 2),
                      "month_total_api_images": round(month["gemini"] + month["clef_usage"] + month["clef_plan"] + month["images_api_medium"], 2)}
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return
    print(f"기록 {len(rows)}줄 ({a.since} ~ {a.until or '지금'}), 하루 {a.per_day}편 × {a.days}일")
    for label, r in res.items():
        print(f"\n[{label}]")
        for k, x in r["per_video"].items():
            print(f"  {k}: {x['calls']}회, ${x['usd']:.4f}")
        print(f"  영상 1편: Gemini·OpenRouter ${r['per_video_gemini_usd']:.4f}, Clef {r['per_video_neurons']} 뉴런, AI 이미지 {r['per_video_images']}장")
        m = r["month"]
        print(f"  한 달: Gemini·OpenRouter ${m['gemini']}, Clef 사용료 ${m['clef_usage']} + 요금제 ${m['clef_plan']}, "
              f"(이미지를 API 로 만들면 +${m['images_api_medium']})")
        print(f"  합계: 이미지 서버(한도형)면 ${r['month_total_subscription_images']}, API 면 ${r['month_total_api_images']}")


if __name__ == "__main__":
    main()
