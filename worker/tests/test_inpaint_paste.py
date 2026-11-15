import shutil
import subprocess

import numpy as np
import pytest
from PIL import Image

from vt.inpaint_providers import inpaint_by_windows

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg が必要")


def test_only_masked_box_is_pasted_back(tmp_path):
    src = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=gray:s=640x360:r=10:d=3",
                    "-pix_fmt", "yuv420p", str(src)], check=True)
    regions = [{"box": [200, 300, 440, 330], "start": 1.0, "end": 2.0}]

    def run_window(job, in_dir, mask_dir, fps):
        out = []
        for p in sorted(in_dir.glob("*.png")):  # 切り抜き全体を赤にして返す
            q = job / f"r_{p.name}"
            Image.new("RGB", Image.open(p).size, (255, 0, 0)).save(q)
            out.append(q)
        return out

    out = tmp_path / "out.mp4"
    inpaint_by_windows(src, regions, out, tmp_path / "work", run_window)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "1.5", "-i", str(out), "-frames:v", "1",
                    str(tmp_path / "f.png")], check=True)
    f = np.asarray(Image.open(tmp_path / "f.png").convert("RGB")).astype(int)
    assert f[315, 320, 0] > 200 and f[315, 320, 1] < 60  # 消した枠の中は差し戻される
    assert abs(f[200, 320, 0] - f[200, 320, 1]) < 10  # 切り抜きの外側 (枠の外) は元のまま
