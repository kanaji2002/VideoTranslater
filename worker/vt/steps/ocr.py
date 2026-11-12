"""工程2: 日本語テロップの読み取りと保存。

Rekognition / Textract は日本語未対応のため、PaddleOCR (lang=japan) をコンテナ内で実行する。
結果は ocr.json (S3) と DynamoDB に保存する。
"""

from __future__ import annotations

import tarfile
from functools import lru_cache

from ..context import JobContext
from ..segments import Detection, build_segments, merge_lines


@lru_cache(maxsize=1)
def _engine():
    from paddleocr import PaddleOCR

    return PaddleOCR(lang="japan", use_angle_cls=False, show_log=False)


def detect(image_path: str) -> list[Detection]:
    result = _engine().ocr(image_path, cls=False)
    dets: list[Detection] = []
    for line in (result[0] or []) if result else []:
        points, (text, score) = line
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        dets.append(Detection(text=text, box=(int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))), score=float(score)))
    return dets


def run(ctx: JobContext) -> dict:
    meta = ctx.read_json("frames.json")
    frames_dir = ctx.local("frames/.keep").parent
    with tarfile.open(ctx.fetch("frames.tar")) as tar:
        tar.extractall(frames_dir, filter="data")

    per_frame = [(f["t"], detect(str(frames_dir / f["file"]))) for f in meta["frames"]]
    segments = merge_lines(build_segments(
        per_frame,
        frame_interval=1.0 / meta["sample_fps"],
        frame_size=(meta["width"], meta["height"]),
    ))
    ctx.write_json("ocr.json", {"width": meta["width"], "height": meta["height"], "segments": segments})
    ctx.set_status("ocr_done", ja_texts=[s["ja"] for s in segments], segment_count=len(segments))
    return {"segments": len(segments)}
