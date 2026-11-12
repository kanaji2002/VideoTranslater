"""ffmpeg / ffprobe ヘルパー。"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


def run(cmd: list[str]) -> str:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"command failed ({proc.returncode}): {' '.join(cmd)}\n{proc.stderr[-4000:]}")
    return proc.stdout


def probe(path: Path) -> dict:
    out = run(
        [
            "ffprobe", "-v", "error", "-print_format", "json",
            "-show_format", "-show_streams", str(path),
        ]
    )
    info = json.loads(out)
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    num, den = (int(x) for x in video.get("avg_frame_rate", "30/1").split("/"))
    return {
        "width": int(video["width"]),
        "height": int(video["height"]),
        "fps": num / den if den else 30.0,
        "duration": float(info["format"]["duration"]),
        "has_audio": any(s["codec_type"] == "audio" for s in info["streams"]),
    }


def normalize(src: Path, dst: Path) -> None:
    """後工程で扱いやすいよう H.264 / yuv420p / 回転情報なしに揃える。"""
    run(
        [
            "ffmpeg", "-y", "-i", str(src),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
            str(dst),
        ]
    )


def extract_frames(src: Path, out_dir: Path, fps: float) -> list[dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    run(
        [
            "ffmpeg", "-y", "-i", str(src), "-vf", f"fps={fps}",
            "-q:v", "2", str(out_dir / "%05d.jpg"),
        ]
    )
    frames = sorted(out_dir.glob("*.jpg"))
    # fps フィルタは各区間の中央付近のフレームを取る: i 番目 ≒ (i + 0.5) / fps 秒
    return [{"file": f.name, "t": round((i + 0.5) / fps, 3)} for i, f in enumerate(frames)]


def frame_at(src: Path, t: float, dst: Path) -> Path:
    run(["ffmpeg", "-y", "-ss", f"{t:.3f}", "-i", str(src), "-frames:v", "1", "-q:v", "2", str(dst)])
    return dst
