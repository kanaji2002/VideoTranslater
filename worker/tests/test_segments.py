from vt.segments import Detection, build_segments, has_japanese, iou


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
