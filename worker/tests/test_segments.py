from vt.segments import Detection, build_segments, has_japanese, in_caption_band, iou, merge_lines


def test_has_japanese():
    assert has_japanese("大さじ1")
    assert not has_japanese("1 tbsp")


def test_iou():
    assert iou((0, 0, 10, 10), (0, 0, 10, 10)) == 1.0
    assert iou((0, 0, 10, 10), (20, 20, 30, 30)) == 0.0


def test_groups_same_caption_across_frames_and_tolerates_ocr_noise():
    box = (100, 600, 500, 660)
    frames = [
        (0.25, []),
        (0.75, [Detection("玉ねぎを切ります", box, 0.95)]),
        (1.25, [Detection("玉ねぎを切リます", (102, 601, 498, 661), 0.90)]),  # OCR 揺れ
        (1.75, []),  # 1フレームの取りこぼし
        (2.25, [Detection("玉ねぎを切ります", box, 0.96)]),
        (2.75, []),
        (3.25, []),
        (3.75, [Detection("塩を少々", (120, 600, 300, 660), 0.9)]),
        (4.25, [Detection("塩を少々", (120, 600, 300, 660), 0.9)]),
    ]
    segs = build_segments(frames, frame_interval=0.5, frame_size=(1080, 1920))
    assert [s["ja"] for s in segs] == ["玉ねぎを切ります", "塩を少々"]
    first = segs[0]
    assert first["start"] == 0.5 and first["end"] == 2.5
    assert first["box"] == [94, 594, 506, 667]


def test_ignores_non_japanese_and_low_score():
    frames = [(0.5, [Detection("ABC", (0, 0, 10, 10), 0.99), Detection("にんじん", (0, 0, 50, 20), 0.3)])]
    assert build_segments(frames, frame_interval=0.5) == []


def _seg(ja, box, start, end):
    return {"id": "x", "ja": ja, "box": list(box), "start": start, "end": end}


def test_merge_split_line_fragments_in_reading_order():
    segs = merge_lines([
        _seg("ふわふわ", (160, 14, 264, 52), 0.5, 4.0),
        _seg("オカツチヤ", (353, 14, 472, 53), 0.5, 4.0),
        _seg("手作り", (269, 16, 348, 51), 0.5, 4.0),
        _seg("強力粉300gにドライイースト3gを加えます", (108, 299, 529, 337), 1.0, 6.0),
    ])
    assert [s["ja"] for s in segs] == ["ふわふわ手作りオカツチヤ", "強力粉300gにドライイースト3gを加えます"]
    assert segs[0]["box"] == [160, 14, 472, 53]


def test_merge_partial_reads_keep_full_text():
    segs = merge_lines([
        _seg("ドライイースト3gを加えます", (246, 299, 529, 335), 1.0, 1.5),
        _seg("強力粉300gにドライイースト3gを加えます", (108, 299, 529, 337), 1.0, 6.0),
        _seg("ト3gを加えます", (368, 299, 528, 335), 2.0, 3.5),
        _seg("ぬる", (120, 303, 182, 334), 6.5, 11.0),
        _seg("ま湯200m1とオリーブオイル大さじ2", (118, 300, 522, 338), 7.5, 11.0),
    ])
    assert [s["ja"] for s in segs] == ["強力粉300gにドライイースト3gを加えます", "ぬるま湯200m1とオリーブオイル大さじ2"]
    assert (segs[0]["start"], segs[0]["end"]) == (1.0, 6.0)


def test_consecutive_captions_at_same_place_stay_separate():
    segs = merge_lines([
        _seg("丸くまとめて表面を張らせます", (163, 297, 473, 339), 22.5, 28.0),
        _seg("ラップをして30分発酵させる", (174, 296, 465, 344), 28.5, 34.0),
    ])
    assert len(segs) == 2


def test_caption_band():
    assert in_caption_band([160, 14, 472, 53], 360)
    assert in_caption_band([108, 299, 529, 337], 360)
    assert not in_caption_band([200, 150, 300, 190], 360)
