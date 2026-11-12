from vt.inpaint_providers import plan_windows


def test_window_adds_context_and_aligned_crop():
    regions = [{"id": "s001", "box": [70, 1010, 650, 1090], "start": 3.0, "end": 5.0}]
    (w,) = plan_windows(regions, fps=30, n_frames=1800, size=(720, 1280))
    assert w["frames"] == (30, 211)  # 前後2秒の文脈
    assert w["mask_frames"] == (90, 151)
    x1, y1, x2, y2 = w["crop"]
    assert (x2 - x1) % 8 == 0 and (y2 - y1) % 8 == 0
    assert x1 <= 70 and y1 <= 1010 and x2 >= 650 and y2 >= 1090 - 8
    assert x2 <= 720 and y2 <= 1280


def test_window_clipped_to_video():
    regions = [{"box": [0, 1200, 720, 1280], "start": 0.0, "end": 1.0}]
    (w,) = plan_windows(regions, fps=30, n_frames=60, size=(720, 1280))
    assert w["frames"] == (0, 60)
    assert w["crop"][0] == 0 and w["crop"][3] <= 1280
