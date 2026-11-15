import numpy as np

from vt.inpaint_providers import temporal_fill


def _scene(n=30, h=40, w=60):
    rng = np.random.default_rng(0)
    bg = rng.integers(0, 255, (h, w, 3), dtype=np.uint8)
    frames = np.repeat(bg[None], n, axis=0).copy()
    masks = np.zeros((n, h, w), bool)
    masks[10:20, 15:25, 10:50] = True
    frames[10:20, 15:25, 10:50] = 255  # 白い文字の帯
    return bg, frames, masks


def test_static_background_is_restored():
    bg, frames, masks = _scene()
    out = temporal_fill(frames, masks, feather_px=0)
    assert np.abs(out[15].astype(int) - bg.astype(int)).mean() < 1.0
    # 文字が出ていないフレームは変えない
    assert (out[0] == frames[0]).all()


def test_moving_object_is_not_copied_from_other_time():
    bg, frames, masks = _scene()
    # 文字が出ている間だけ、帯の上下を含む列 30〜40 を赤い物体が通る
    frames[10:20, 5:35, 30:40] = (200, 0, 0)
    frames[10:20, 15:25, 10:50] = 255
    out = temporal_fill(frames, masks, feather_px=0)
    # その列は別時刻の背景ではなく、今のフレームの上下 (赤) から補われる
    assert out[15, 20, 35, 0] > 150 and out[15, 20, 35, 1] < 60
    # 物体のない列は背景に戻る
    assert np.abs(out[15, 20, 15].astype(int) - bg[20, 15].astype(int)).max() < 2
