"""工程1: 元動画の取得・正規化とフレーム抽出。"""

from __future__ import annotations

import os
import tarfile

from .. import media
from ..context import JobContext

SAMPLE_FPS = float(os.environ.get("SAMPLE_FPS", "4"))
MAX_DURATION_SEC = float(os.environ.get("MAX_DURATION_SEC", "180"))


def run(ctx: JobContext) -> dict:
    raw = ctx.download(ctx.input_key, ctx.local("raw" + os.path.splitext(ctx.input_key)[1]))
    info = media.probe(raw)
    if info["duration"] > MAX_DURATION_SEC:
        raise ValueError(f"動画が長すぎます: {info['duration']:.1f}s > {MAX_DURATION_SEC}s")

    source = ctx.local("source.mp4")
    media.normalize(raw, source)
    ctx.upload(source, ctx.key("source.mp4"), "video/mp4")

    frames_dir = ctx.local("frames/.keep").parent
    frames = media.extract_frames(source, frames_dir, SAMPLE_FPS)
    tar_path = ctx.local("frames.tar")
    with tarfile.open(tar_path, "w") as tar:
        for f in frames:
            tar.add(frames_dir / f["file"], arcname=f["file"])
    ctx.upload(tar_path, ctx.key("frames.tar"))

    meta = {**info, "sample_fps": SAMPLE_FPS, "frames": frames}
    ctx.write_json("frames.json", meta)
    return {"duration": info["duration"], "frames": len(frames), "width": info["width"], "height": info["height"]}
