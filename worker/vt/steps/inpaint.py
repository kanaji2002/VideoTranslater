"""工程4: 日本語テロップの除去と背景の補間 (専用ツール API)。

QA で NG になり再翻訳した場合も消す領域は変わらないので、
同じ領域で処理済みの clean.mp4 があれば再利用する。
"""

from __future__ import annotations

import hashlib
import json

from ..context import JobContext
from ..inpaint_providers import get_provider


def run(ctx: JobContext) -> dict:
    translation = ctx.read_json("translation.json")
    regions = [
        {"id": s["id"], "box": s["box"], "start": s["start"], "end": s["end"]}
        for s in translation["segments"]
        if s.get("is_overlay", True)
    ]
    digest = hashlib.sha256(json.dumps(regions, sort_keys=True).encode()).hexdigest()

    if ctx.exists(ctx.key("clean.json")) and ctx.read_json("clean.json").get("digest") == digest:
        return {"regions": len(regions), "cached": True}

    source = ctx.fetch("source.mp4")
    clean = ctx.local("clean.mp4")
    get_provider().remove_text(source, regions, clean, ctx.local("inpaint/.keep").parent)
    ctx.upload(clean, ctx.key("clean.mp4"), "video/mp4")
    ctx.write_json("clean.json", {"digest": digest, "regions": regions})
    return {"regions": len(regions), "cached": False}
