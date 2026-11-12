"""工程3: OCR 結果の補正と英訳 (Bedrock / Claude)。

- 各テロップの切り抜き画像を添えて、OCR の誤認識を補正させる
- 元の枠に収まる長さを目安に、料理動画向けの自然な英語に訳す
- 2回目以降 (QA で NG) は、QA の指摘を渡して訳し直す
"""

from __future__ import annotations

import json
import os
import tarfile

from PIL import Image

from .. import llm
from ..context import JobContext

MAX_IMAGES = int(os.environ.get("TRANSLATE_MAX_IMAGES", "40"))

SYSTEM = """あなたは料理レシピ動画のローカライズ担当です。
動画から OCR で読み取った日本語テロップを受け取り、次を行います。
1. 添付の切り抜き画像を見て OCR の誤認識を直す (ja_corrected)。
2. 英語圏の視聴者向けに、短く自然な英語へ訳す (en)。
   - 料理用語・分量・単位は英語圏で通じる表記にする (例: 大さじ1 -> 1 tbsp, 小さじ1/2 -> 1/2 tsp)。
   - 元のテロップ枠に収まるよう、max_chars を超えないことを最優先にする。
   - 固有名詞・商品名はローマ字表記か一般名詞に置き換える。
3. 後から載せたテロップではなく、映像内の看板・商品パッケージなどの文字なら is_overlay を false にする。
review_feedback がある場合は、その指摘を必ず反映すること。"""

SCHEMA = {
    "type": "object",
    "properties": {
        "segments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "ja_corrected": {"type": "string"},
                    "en": {"type": "string"},
                    "is_overlay": {"type": "boolean"},
                },
                "required": ["id", "ja_corrected", "en", "is_overlay"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["segments"],
    "additionalProperties": False,
}


def max_chars(box: list[int]) -> int:
    """枠の幅と高さから、英語で収まる文字数の目安を出す (1行・平均字幅 0.55em)。"""
    w, h = box[2] - box[0], box[3] - box[1]
    font_px = max(12, h * 0.75)
    return max(8, int(w / (font_px * 0.55)))


def _crops(ctx: JobContext, segments: list[dict]) -> dict[str, str]:
    meta = ctx.read_json("frames.json")
    frames_dir = ctx.local("frames/.keep").parent
    if not any(frames_dir.glob("*.jpg")):
        with tarfile.open(ctx.fetch("frames.tar")) as tar:
            tar.extractall(frames_dir, filter="data")
    crops: dict[str, str] = {}
    for seg in segments[:MAX_IMAGES]:
        mid = (seg["start"] + seg["end"]) / 2
        frame = min(meta["frames"], key=lambda f: abs(f["t"] - mid))
        img = Image.open(frames_dir / frame["file"]).convert("RGB")
        crop_path = ctx.local(f"crops/{seg['id']}.jpg")
        img.crop(tuple(seg["box"])).save(crop_path, quality=90)
        crops[seg["id"]] = str(crop_path)
    return crops


def run(ctx: JobContext) -> dict:
    ocr = ctx.read_json("ocr.json")
    segments = ocr["segments"]
    if not segments:
        ctx.write_json("translation.json", {"segments": []})
        return {"segments": 0}

    feedback = None
    if ctx.attempt > 0 and ctx.exists(ctx.key("qa.json")):
        feedback = ctx.read_json("qa.json").get("issues")

    crops = _crops(ctx, segments)
    content: list[dict] = []
    items = []
    for seg in segments:
        items.append(
            {
                "id": seg["id"],
                "ocr_ja": seg["ja"],
                "start": seg["start"],
                "end": seg["end"],
                "max_chars": max_chars(seg["box"]),
            }
        )
        if seg["id"] in crops:
            content.append({"type": "text", "text": f"[{seg['id']}] の切り抜き:"})
            content.append(llm.image_block(crops[seg["id"]]))

    payload = {"segments": items}
    if feedback:
        payload["review_feedback"] = feedback
    content.append({"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2)})

    result = llm.ask_json(SYSTEM, content, SCHEMA, effort="medium")
    by_id = {s["id"]: s for s in result["segments"]}

    merged = []
    for seg in segments:
        tr = by_id.get(seg["id"])
        if tr is None:
            raise RuntimeError(f"翻訳結果に {seg['id']} がありません")
        merged.append({**seg, **tr, "max_chars": max_chars(seg["box"])})

    ctx.write_json("translation.json", {"attempt": ctx.attempt, "segments": merged})
    ctx.set_status("translated", en_texts=[s["en"] for s in merged])
    return {"segments": len(merged), "attempt": ctx.attempt}
