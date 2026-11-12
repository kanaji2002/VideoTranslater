"""英語テキストを元テロップの枠に収めて画像化する。"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT_CANDIDATES = [
    os.environ.get("FONT_PATH", ""),
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


@lru_cache(maxsize=1)
def font_path() -> str:
    for p in FONT_CANDIDATES:
        if p and Path(p).exists():
            return p
    raise FileNotFoundError("英語用フォントが見つかりません (FONT_PATH を指定してください)")


@lru_cache(maxsize=256)
def _font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(font_path(), size)


def _wrap(text: str, font: ImageFont.FreeTypeFont, max_w: int, max_lines: int) -> list[str] | None:
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if font.getlength(trial) <= max_w:
            cur = trial
        else:
            if not cur:  # 1単語で幅オーバー
                return None
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines if len(lines) <= max_lines else None


def fit_text(text: str, box_w: int, box_h: int, *, max_lines: int = 2, min_size: int = 14) -> dict:
    """枠に収まる最大のフォントサイズと改行位置を探す。収まらなければ overflow=True。"""
    start = max(min_size, int(box_h * 0.8))
    for size in range(start, min_size - 1, -1):
        font = _font(size)
        line_h = int(size * 1.2)
        for n in range(1, max_lines + 1):
            if line_h * n > box_h:
                break
            lines = _wrap(text, font, box_w, n)
            if lines:
                return {"size": size, "lines": lines, "overflow": False}
    font = _font(min_size)
    lines = _wrap(text, font, box_w, 99) or [text]
    return {"size": min_size, "lines": lines, "overflow": True}


def render_text_png(
    text: str,
    box: list[int],
    out: Path,
    frame_size: tuple[int, int],
    *,
    fill=(255, 255, 255, 255),
    stroke=(0, 0, 0, 255),
) -> dict:
    """box (x1,y1,x2,y2) に収まる英語テキスト画像 (透過 PNG) を作り、貼り付け位置を返す。"""
    x1, y1, x2, y2 = box
    fit = fit_text(text, x2 - x1, y2 - y1)
    font = _font(fit["size"])
    line_h = int(fit["size"] * 1.2)
    stroke_w = max(2, fit["size"] // 12)
    w = int(max(font.getlength(line) for line in fit["lines"])) + stroke_w * 2 + 4
    h = line_h * len(fit["lines"]) + stroke_w * 2 + 4
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    for i, line in enumerate(fit["lines"]):
        lw = font.getlength(line)
        draw.text(
            ((w - lw) / 2, stroke_w + 2 + i * line_h),
            line,
            font=font,
            fill=fill,
            stroke_width=stroke_w,
            stroke_fill=stroke,
        )
    img.save(out)

    # 元テロップの中心に合わせて配置し、フレーム外にはみ出さないよう調整
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    fw, fh = frame_size
    px = int(min(max(0, cx - w / 2), fw - w))
    py = int(min(max(0, cy - h / 2), fh - h))
    return {**fit, "x": px, "y": py, "w": w, "h": h}
