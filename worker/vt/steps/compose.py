"""工程5: 文字除去済み動画に英語テキストを合成する。"""

from __future__ import annotations

from .. import media
from ..context import JobContext
from ..render import render_text_png


def run(ctx: JobContext) -> dict:
    translation = ctx.read_json("translation.json")
    clean = ctx.fetch("clean.mp4")
    info = media.probe(clean)
    frame_size = (info["width"], info["height"])

    overlays, placements = [], []
    for seg in translation["segments"]:
        if not seg.get("is_overlay", True) or not seg["en"].strip():
            continue
        png = ctx.local(f"overlays/{seg['id']}.png")
        place = render_text_png(seg["en"], seg["box"], png, frame_size)
        overlays.append((png, seg, place))
        placements.append({"id": seg["id"], **place})

    out = ctx.local("output.mp4")
    if overlays:
        cmd = ["ffmpeg", "-y", "-i", str(clean)]
        for png, _, _ in overlays:
            cmd += ["-i", str(png)]
        chain, prev = [], "0:v"
        for i, (_, seg, place) in enumerate(overlays, start=1):
            label = f"v{i}"
            chain.append(
                f"[{prev}][{i}:v]overlay=x={place['x']}:y={place['y']}"
                f":enable='between(t,{seg['start']},{seg['end']})'[{label}]"
            )
            prev = label
        cmd += [
            "-filter_complex", ";".join(chain), "-map", f"[{prev}]", "-map", "0:a?",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
            "-c:a", "copy", "-movflags", "+faststart", str(out),
        ]
        media.run(cmd)
    else:
        out.write_bytes(clean.read_bytes())

    ctx.upload(out, ctx.key("output.mp4"), "video/mp4")
    ctx.write_json("compose.json", {"attempt": ctx.attempt, "placements": placements})
    return {"overlays": len(overlays), "overflow": sum(p["overflow"] for p in placements)}
