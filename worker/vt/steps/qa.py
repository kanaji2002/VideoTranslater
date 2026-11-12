"""工程6: エージェントによる品質確認。

2段階で確認する:
1. 機械チェック: 英文が枠からはみ出していないか / 出力フレームに日本語テロップが残っていないか (OCR)
2. Claude による目視チェック: 完成フレームを見て、消し残し・読みやすさ・訳の正確さを判定

NG の場合は issues を qa.json に保存し、Step Functions が再翻訳 -> 再合成 -> 再QA を回す。
"""

from __future__ import annotations

import json
import os

from PIL import Image

from .. import llm, media
from ..context import JobContext
from ..segments import has_japanese

MAX_FRAMES = int(os.environ.get("QA_MAX_FRAMES", "20"))
PASS_SCORE = int(os.environ.get("QA_PASS_SCORE", "80"))

SYSTEM = """あなたは料理レシピ動画の英語ローカライズの品質管理担当です。
日本語テロップを消して英語テキストを合成した完成動画のフレームと、各テロップの原文・訳文を受け取ります。
各フレームを実際に見て、次の観点で厳しく確認してください。
- japanese_remaining: 日本語の文字や消し跡がはっきり残っている
- inpaint_artifact: 文字を消した跡が目立つ (ぼやけ・色むら)
- overflow: 英文が切れている、画面外にはみ出している、他の要素と重なって読めない
- readability: 文字が小さすぎる、背景と同化して読みにくい
- translation: 誤訳、不自然な英語、分量・単位の誤り
- timing: 表示タイミングが映像と合っていない
軽微な問題は severity=minor、視聴者が明らかに気付く問題は major にしてください。
major が1つでもあれば passed=false にしてください。
translation / overflow の指摘には、短く言い換えた英文などの具体的な suggestion を付けてください。"""

SCHEMA = {
    "type": "object",
    "properties": {
        "passed": {"type": "boolean"},
        "score": {"type": "integer", "description": "0-100"},
        "summary": {"type": "string"},
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "segment_id": {"type": "string"},
                    "type": {
                        "type": "string",
                        "enum": [
                            "japanese_remaining", "inpaint_artifact", "overflow",
                            "readability", "translation", "timing",
                        ],
                    },
                    "severity": {"type": "string", "enum": ["minor", "major"]},
                    "detail": {"type": "string"},
                    "suggestion": {"type": "string"},
                },
                "required": ["segment_id", "type", "severity", "detail", "suggestion"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["passed", "score", "summary", "issues"],
    "additionalProperties": False,
}


def _leftover_japanese(frame_path, box) -> list[str]:
    try:
        from .ocr import detect
    except ImportError:  # PaddleOCR が無い環境 (ローカルテスト) ではスキップ
        return []
    crop_path = str(frame_path).replace(".jpg", "_crop.jpg")
    Image.open(frame_path).crop(tuple(box)).save(crop_path)
    return [d.text for d in detect(crop_path) if d.score >= 0.7 and has_japanese(d.text)]


def mechanical_checks(ctx: JobContext, segments: list[dict], placements: dict, frames: dict) -> list[dict]:
    issues = []
    for seg in segments:
        place = placements.get(seg["id"])
        if place and place["overflow"]:
            issues.append(
                {
                    "segment_id": seg["id"],
                    "type": "overflow",
                    "severity": "major",
                    "detail": f"英文が枠に収まりません (最大 {seg.get('max_chars')} 文字程度)",
                    "suggestion": "より短い英語に言い換える",
                }
            )
        if seg["id"] in frames and seg.get("is_overlay", True):
            left = _leftover_japanese(frames[seg["id"]], seg["box"])
            if left:
                issues.append(
                    {
                        "segment_id": seg["id"],
                        "type": "japanese_remaining",
                        "severity": "major",
                        "detail": f"出力フレームで日本語を検出: {' / '.join(left)}",
                        "suggestion": "除去領域を広げて再処理する",
                    }
                )
    return issues


def run(ctx: JobContext) -> dict:
    translation = ctx.read_json("translation.json")
    segments = translation["segments"]
    placements = {p["id"]: p for p in ctx.read_json("compose.json")["placements"]}
    output = ctx.fetch("output.mp4")

    frames = {}
    for seg in segments[:MAX_FRAMES]:
        t = (seg["start"] + seg["end"]) / 2
        frames[seg["id"]] = media.frame_at(output, t, ctx.local(f"qa/{seg['id']}.jpg"))

    issues = mechanical_checks(ctx, segments, placements, frames)

    content: list[dict] = []
    for seg in segments[:MAX_FRAMES]:
        path = frames[seg["id"]]
        img = Image.open(path)
        if img.width > 1280:
            img.thumbnail((1280, 1280))
            img.save(path, quality=90)
        content.append({"type": "text", "text": f"[{seg['id']}] {seg['start']:.1f}s-{seg['end']:.1f}s の完成フレーム:"})
        content.append(llm.image_block(path))
    content.append(
        {
            "type": "text",
            "text": json.dumps(
                {
                    "segments": [
                        {k: s.get(k) for k in ("id", "ja_corrected", "en", "box", "start", "end", "is_overlay")}
                        for s in segments
                    ],
                    "mechanical_issues": issues,
                },
                ensure_ascii=False,
                indent=2,
            ),
        }
    )
    review = llm.ask_json(SYSTEM, content, SCHEMA, effort="high")

    all_issues = issues + review["issues"]
    passed = (
        review["passed"]
        and review["score"] >= PASS_SCORE
        and not any(i["severity"] == "major" for i in all_issues)
    )
    qa = {
        "attempt": ctx.attempt,
        "passed": passed,
        "score": review["score"],
        "summary": review["summary"],
        "issues": all_issues,
    }
    ctx.write_json("qa.json", qa)
    ctx.write_json(f"qa_attempt{ctx.attempt}.json", qa)
    ctx.set_status("qa_done", qa_passed=passed, qa_score=review["score"])
    return {"passed": passed, "score": review["score"], "issues": len(all_issues)}
