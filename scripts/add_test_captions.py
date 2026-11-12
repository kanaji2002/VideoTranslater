"""検証用: 手元の動画に日本語テロップを焼き込み、正解データ (JSON) も一緒に出す。

    python scripts/add_test_captions.py 入力.mp4 [出力.mp4]

出力:
  <出力>.mp4           テロップ入りの動画 (S3 の input/ に置いて試す)
  <出力>.truth.json    入れたテロップの文字・時刻・位置 (OCR と翻訳の答え合わせ用)

テロップの内容は CAPTIONS を書き換えてください。動画が短いときは、収まる分だけ入れます。
必要なもの: ffmpeg, Pillow
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# (開始秒, 終了秒, 文字, 位置) 位置は bottom / top。食材ルールの確認用にみりん・片栗粉などを入れている
CAPTIONS = [
    (0.5, 3.5, "今日は簡単！肉じゃが", "top"),
    (1.0, 4.0, "じゃがいもを一口大に切ります", "bottom"),
    (4.5, 7.5, "醤油 大さじ2・みりん 大さじ2", "bottom"),
    (8.0, 11.0, "中火で10分煮込みます", "bottom"),
    (11.5, 14.5, "片栗粉 小さじ1でとろみをつける", "bottom"),
    (15.0, 18.0, "大葉をのせて完成！", "bottom"),
]

FONTS = [
    "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",  # macOS
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",  # Linux (fonts-noto-cjk)
    "C:/Windows/Fonts/meiryob.ttc",
]


def font(size: int) -> ImageFont.FreeTypeFont:
    for f in FONTS:
        if Path(f).exists():
            return ImageFont.truetype(f, size)
    sys.exit("日本語フォントが見つかりません。FONTS にパスを追加してください。")


def probe(path: Path) -> tuple[int, int, float]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height:format=duration",
         "-of", "json", str(path)],
        check=True, capture_output=True, text=True,
    ).stdout
    j = json.loads(out)
    return j["streams"][0]["width"], j["streams"][0]["height"], float(j["format"]["duration"])


def main() -> None:
    src = Path(sys.argv[1])
    dst = Path(sys.argv[2]) if len(sys.argv) > 2 else src.with_name(src.stem + "_ja.mp4")
    w, h, dur = probe(src)
    work = dst.with_suffix("")
    work.mkdir(parents=True, exist_ok=True)

    size = max(16, int(h * 0.06))
    f = font(size)
    inputs, filters, truth = [], [], []
    last = "[0:v]"
    caps = [c for c in CAPTIONS if c[1] <= dur]
    for i, (start, end, text, pos) in enumerate(caps, 1):
        x1, y1, x2, y2 = f.getbbox(text, stroke_width=max(2, size // 10))
        tw, th = x2 - x1, y2 - y1
        x = (w - tw) // 2
        y = int(h * 0.06) if pos == "top" else h - th - int(h * 0.08)
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ImageDraw.Draw(img).text((x - x1, y - y1), text, font=f, fill="white",
                                 stroke_width=max(2, size // 10), stroke_fill="black")
        png = work / f"cap{i}.png"
        img.save(png)
        inputs += ["-i", str(png)]
        out = f"[v{i}]"
        filters.append(f"{last}[{i}:v]overlay=enable='between(t,{start},{end})'{out}")
        last = out
        truth.append({"id": f"c{i}", "ja": text, "start": start, "end": end, "box": [x, y, x + tw, y + th]})

    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), *inputs]
    if filters:
        cmd += ["-filter_complex", ";".join(filters), "-map", last]
    else:
        cmd += ["-map", "0:v"]
    cmd += ["-map", "0:a?", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", "-c:a", "copy", str(dst)]
    subprocess.run(cmd, check=True)

    truth_path = dst.with_suffix(".truth.json")
    truth_path.write_text(json.dumps({"video": dst.name, "width": w, "height": h, "captions": truth},
                                     ensure_ascii=False, indent=2))
    for p in work.glob("cap*.png"):
        p.unlink()
    work.rmdir()
    print(f"{dst}\n{truth_path}  ({len(truth)} 個のテロップ)")


if __name__ == "__main__":
    main()
