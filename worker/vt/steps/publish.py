"""工程7: 保存。

QA 合格 -> output/{元ファイル名}_en.mp4
QA 不合格 (再試行上限) -> review/{元ファイル名}_en.mp4 (人の確認待ち)
あわせて日本語・英語テキストと QA 結果を JSON で保存し、DynamoDB のステータスを更新する。
"""

from __future__ import annotations

import json
import os
from pathlib import PurePosixPath

from ..context import JobContext


def run(ctx: JobContext) -> dict:
    status = os.environ.get("PUBLISH_STATUS", "approved")
    prefix = "output" if status == "approved" else "review"
    stem = PurePosixPath(ctx.input_key).stem

    video_key = f"{prefix}/{stem}_en.mp4"
    meta_key = f"{prefix}/{stem}_en.json"
    ctx.copy(ctx.key("output.mp4"), video_key)

    translation = ctx.read_json("translation.json")
    qa = ctx.read_json("qa.json") if ctx.exists(ctx.key("qa.json")) else None
    meta = {
        "job_id": ctx.job_id,
        "input_key": ctx.input_key,
        "status": status,
        "video_key": video_key,
        "segments": [
            {k: s.get(k) for k in ("id", "start", "end", "box", "ja", "ja_corrected", "en", "is_overlay")}
            for s in translation["segments"]
        ],
        "qa": qa,
    }
    meta_path = ctx.local("result.json")
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    ctx.upload(meta_path, meta_key, "application/json")
    ctx.set_status(status, output_key=video_key, metadata_key=meta_key)
    return {"status": status, "video_key": video_key, "metadata_key": meta_key, "qa_score": qa and qa["score"]}
