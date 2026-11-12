"""フレームごとの OCR 結果を「テロップ区間」(文字・位置・開始/終了時刻) にまとめる純粋ロジック。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

JP_RE = re.compile(r"[぀-ヿ㐀-鿿ｦ-ﾟ]")

Box = tuple[int, int, int, int]  # x1, y1, x2, y2


@dataclass
class Detection:
    text: str
    box: Box
    score: float


@dataclass
class Track:
    id: int
    box: Box
    start: float
    end: float
    texts: list[tuple[str, float]] = field(default_factory=list)  # (text, score)
    boxes: list[Box] = field(default_factory=list)
    last_seen_index: int = 0

    def best_text(self) -> str:
        # 最も多く出現した文字列 (同数ならスコア合計が高いもの)
        tally: dict[str, float] = {}
        count: dict[str, int] = {}
        for t, s in self.texts:
            tally[t] = tally.get(t, 0.0) + s
            count[t] = count.get(t, 0) + 1
        return max(tally, key=lambda t: (count[t], tally[t]))

    def union_box(self) -> Box:
        xs1, ys1, xs2, ys2 = zip(*self.boxes)
        return min(xs1), min(ys1), max(xs2), max(ys2)


def has_japanese(text: str) -> bool:
    return bool(JP_RE.search(text))


def iou(a: Box, b: Box) -> float:
    ix1, iy1, ix2, iy2 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
    if inter == 0:
        return 0.0
    area = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / area


def similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def build_segments(
    frames: list[tuple[float, list[Detection]]],
    frame_interval: float,
    *,
    min_score: float = 0.6,
    iou_threshold: float = 0.4,
    text_threshold: float = 0.5,
    max_gap_frames: int = 1,
    min_duration: float = 0.3,
    pad_px: int = 6,
    frame_size: tuple[int, int] | None = None,
) -> list[dict]:
    """frames: [(時刻, [Detection,...]), ...] (時刻順)。

    同じ位置・似た文字のテロップを連続フレームで1つの区間にまとめる。
    max_gap_frames までの検出漏れ (OCR の取りこぼし) は同じ区間として扱う。
    """
    active: list[Track] = []
    done: list[Track] = []
    next_id = 1

    for idx, (t, dets) in enumerate(frames):
        dets = [d for d in dets if d.score >= min_score and has_japanese(d.text)]
        matched: set[int] = set()
        for d in dets:
            best, best_score = None, 0.0
            for tr in active:
                if tr.id in matched:
                    continue
                o = iou(d.box, tr.box)
                s = similar(d.text, tr.texts[-1][0])
                if o >= iou_threshold and s >= text_threshold and o + s > best_score:
                    best, best_score = tr, o + s
            if best is None:
                best = Track(id=next_id, box=d.box, start=t - frame_interval / 2, end=t + frame_interval / 2)
                next_id += 1
                active.append(best)
            best.box = d.box
            best.end = t + frame_interval / 2
            best.texts.append((d.text, d.score))
            best.boxes.append(d.box)
            best.last_seen_index = idx
            matched.add(best.id)

        still: list[Track] = []
        for tr in active:
            (still if idx - tr.last_seen_index <= max_gap_frames else done).append(tr)
        active = still

    done.extend(active)
    out = []
    for tr in sorted(done, key=lambda tr: (tr.start, tr.union_box()[1])):
        if tr.end - tr.start < min_duration:
            continue
        x1, y1, x2, y2 = tr.union_box()
        x1, y1, x2, y2 = x1 - pad_px, y1 - pad_px, x2 + pad_px, y2 + pad_px
        if frame_size:
            w, h = frame_size
            x1, y1, x2, y2 = max(0, x1), max(0, y1), min(w, x2), min(h, y2)
        out.append(
            {
                "id": f"s{len(out) + 1:03d}",
                "ja": tr.best_text(),
                "box": [x1, y1, x2, y2],
                "start": round(max(0.0, tr.start), 3),
                "end": round(tr.end, 3),
                "samples": len(tr.texts),
            }
        )
    return out


def _same_line(a: dict, b: dict) -> bool:
    """同じ行のテロップの断片か (縦位置がほぼ同じ・時間が重なる・横に離れすぎていない)。"""
    ax1, ay1, ax2, ay2 = a["box"]
    bx1, by1, bx2, by2 = b["box"]
    v_overlap = min(ay2, by2) - max(ay1, by1)
    if v_overlap < 0.5 * min(ay2 - ay1, by2 - by1):
        return False
    if min(a["end"], b["end"]) <= max(a["start"], b["start"]):
        return False
    gap = max(ax1, bx1) - min(ax2, bx2)  # 負なら横に重なっている
    return gap <= 1.5 * max(ay2 - ay1, by2 - by1)


def _join_text(a: dict, b: dict) -> str:
    ta, tb = a["ja"], b["ja"]
    if ta in tb or similar(ta, tb) >= 0.6 and len(tb) >= len(ta):
        return tb
    if tb in ta or similar(ta, tb) >= 0.6:
        return ta
    # 別々の断片なら、左にあるものから順につなぐ
    ca, cb = (a["box"][0] + a["box"][2]) / 2, (b["box"][0] + b["box"][2]) / 2
    return ta + tb if ca <= cb else tb + ta


def merge_lines(segments: list[dict]) -> list[dict]:
    """OCR が 1 行のテロップを複数に分けて読んだもの (「ふわふわ」「手作り」「フォカッチャ」や、
    一部だけ読めた短い区間) を 1 つの区間にまとめる。まとめた区間は枠・時間とも和集合にする。"""
    segs = [dict(s) for s in segments]
    changed = True
    while changed:
        changed = False
        for i in range(len(segs)):
            for j in range(i + 1, len(segs)):
                a, b = segs[i], segs[j]
                if not _same_line(a, b):
                    continue
                segs[i] = {
                    **a,
                    "ja": _join_text(a, b),
                    "box": [min(a["box"][0], b["box"][0]), min(a["box"][1], b["box"][1]),
                            max(a["box"][2], b["box"][2]), max(a["box"][3], b["box"][3])],
                    "start": min(a["start"], b["start"]),
                    "end": max(a["end"], b["end"]),
                    "samples": a.get("samples", 0) + b.get("samples", 0),
                }
                del segs[j]
                changed = True
                break
            if changed:
                break
    segs.sort(key=lambda s: (s["start"], s["box"][1]))
    for n, s in enumerate(segs, 1):
        s["id"] = f"s{n:03d}"
    return segs


def in_caption_band(box: list[int], frame_h: int, *, top: float = 0.25, bottom: float = 0.70) -> bool:
    """テロップが入りやすい画面の上端・下端の帯にあるか。"""
    cy = (box[1] + box[3]) / 2
    return cy <= frame_h * top or cy >= frame_h * bottom
