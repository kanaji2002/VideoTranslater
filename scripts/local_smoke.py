"""ローカルでの動作確認 (AWS / PaddleOCR / Bedrock なし)。

日本語テロップ入りのテスト動画を作り、
  extract -> (OCR と翻訳は固定値で代用) -> inpaint(ffmpeg_delogo) -> compose
を実行して、完成フレームを work/smoke/ に書き出す。

    python scripts/local_smoke.py
"""

import json
import os
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "worker"))

WORK = ROOT / "work" / "smoke"
STORE = WORK / "store"
JA_FONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"


def make_input() -> Path:
    WORK.mkdir(parents=True, exist_ok=True)
    caption = WORK / "caption.png"
    img = Image.new("RGBA", (720, 1280), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.text((360, 1050), "玉ねぎを薄切りにします", font=ImageFont.truetype(JA_FONT, 52), anchor="mm",
           fill="white", stroke_width=4, stroke_fill="black")
    img.save(caption)
    src = STORE / "input" / "sample.mp4"
    src.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc2=s=720x1280:r=30:d=6",
         "-f", "lavfi", "-i", "sine=f=440:d=6", "-i", str(caption),
         "-filter_complex", "[0:v][2:v]overlay=enable='between(t,1,4)'",
         "-shortest", "-pix_fmt", "yuv420p", str(src)],
        check=True, capture_output=True,
    )
    return src


def main():
    make_input()
    os.environ.update(JOB_ID="smoke", INPUT_KEY="input/sample.mp4", LOCAL_ROOT=str(STORE),
                      WORK_DIR=str(WORK / "tmp"), INPAINT_PROVIDER="ffmpeg_delogo")
    from vt.context import JobContext
    from vt.steps import compose, extract, inpaint

    ctx = JobContext.from_env()
    print("extract:", extract.run(ctx))

    # OCR と翻訳の代わりに固定値 (本番は PaddleOCR と Bedrock)
    seg = {"id": "s001", "ja": "玉ねぎを薄切りにします", "ja_corrected": "玉ねぎを薄切りにします",
           "en": "Thinly slice the onion", "is_overlay": True,
           "box": [70, 1010, 650, 1090], "start": 1.0, "end": 4.0}
    ctx.write_json("translation.json", {"segments": [seg]})
    print("inpaint:", inpaint.run(ctx))
    print("compose:", compose.run(ctx))

    out = STORE / "jobs" / "smoke" / "output.mp4"
    for name, video in [("before", STORE / "input" / "sample.mp4"), ("clean", STORE / "jobs/smoke/clean.mp4"), ("after", out)]:
        subprocess.run(["ffmpeg", "-y", "-ss", "2.5", "-i", str(video), "-frames:v", "1", str(WORK / f"{name}.jpg")],
                       check=True, capture_output=True)
    print("frames written to", WORK)


if __name__ == "__main__":
    main()
