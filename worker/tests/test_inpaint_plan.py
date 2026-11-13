from vt.inpaint_providers import check_filled, plan_windows, select_output


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


def test_overlapping_lines_are_merged():
    # 同じ時間に出る 2 行のテロップは 1 つの範囲にまとめる
    regions = [
        {"id": "a", "box": [100, 900, 600, 960], "start": 3.0, "end": 5.0},
        {"id": "b", "box": [100, 970, 600, 1030], "start": 3.0, "end": 5.5},
        {"id": "c", "box": [100, 100, 600, 160], "start": 3.0, "end": 5.0},  # 上のテロップは離れているので別
    ]
    wins = plan_windows(regions, fps=30, n_frames=1800, size=(720, 1280))
    assert len(wins) == 2
    merged = next(w for w in wins if "a" in w["id"])
    assert merged["id"] in ("a,b", "b,a") and len(merged["masks"]) == 2
    assert merged["crop"][1] <= 900 and merged["crop"][3] >= 1030 - 8
    assert merged["mask_frames"] == (90, 166)


def test_far_apart_in_time_not_merged():
    regions = [
        {"box": [100, 900, 600, 960], "start": 1.0, "end": 2.0},
        {"box": [100, 900, 600, 960], "start": 20.0, "end": 21.0},
    ]
    assert len(plan_windows(regions, fps=30, n_frames=1800, size=(720, 1280))) == 2


def test_long_caption_split_into_chunks():
    # 20 秒続くテロップは 5 秒ずつに分け、貼り戻す範囲 (core) は重ならない
    regions = [{"box": [100, 900, 600, 960], "start": 2.0, "end": 22.0}]
    wins = plan_windows(regions, fps=30, n_frames=1800, size=(720, 1280), max_sec=5.0)
    assert len(wins) == 5
    cores = [w["core"] for w in wins]
    assert cores[0][0] == 60 and cores[-1][1] == 661
    assert all(a[1] == b[0] for a, b in zip(cores, cores[1:]))
    assert all(w["frames"][1] - w["frames"][0] <= 150 + 120 for w in wins)


def test_select_output_prefers_inpainted_video():
    urls = ["https://r/x/masked_in.mp4", "https://r/x/inpaint_out.mp4"]
    assert select_output(urls).endswith("inpaint_out.mp4")
    assert select_output(list(reversed(urls))).endswith("inpaint_out.mp4")
    assert select_output("https://r/x/out.mp4").endswith("out.mp4")
    assert select_output(["https://r/x/mask.mp4", "https://r/x/result.mp4"]).endswith("result.mp4")


def test_check_filled_rejects_black_region(tmp_path):
    import pytest
    from PIL import Image, ImageDraw

    mask = Image.new("L", (100, 40), 0)
    ImageDraw.Draw(mask).rectangle((20, 10, 80, 30), fill=255)
    mask.save(tmp_path / "m.png")
    black = Image.new("RGB", (100, 40), (200, 180, 150))
    ImageDraw.Draw(black).rectangle((20, 10, 80, 30), fill=(0, 0, 0))
    black.save(tmp_path / "black.png")
    with pytest.raises(RuntimeError):
        check_filled([tmp_path / "black.png"], [tmp_path / "m.png"])
    Image.new("RGB", (100, 40), (200, 180, 150)).save(tmp_path / "ok.png")
    check_filled([tmp_path / "ok.png"], [tmp_path / "m.png"])
