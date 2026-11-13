"""文字除去 / インペイントの実装切り替え。

INPAINT_PROVIDER で選ぶ:
- ffmpeg_delogo : 外部 API なしで動く簡易版 (delogo フィルタで周囲から補間)。動作確認用。
- propainter_local : 同じコンテナ内の ProPainter を GPU で実行 (AWS Batch の GPU インスタンス用)。
- replicate     : Replicate 上の ProPainter (動画 + マスク動画で背景を補間)。
- http          : 専用ツールの API。API 仕様が決まったら HttpInpaintProvider を合わせて修正する。

どの実装も「元動画 + 消したい領域 (box と時間) -> 文字を消した動画」という同じ形にしている。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Protocol

from . import media


class InpaintProvider(Protocol):
    def remove_text(self, video: Path, regions: list[dict], out: Path, work_dir: Path) -> None: ...


def make_mask_video(video: Path, regions: list[dict], out: Path) -> Path:
    """消したい領域を白、それ以外を黒にしたマスク動画を作る (マスク動画を要求する API 用)。"""
    info = media.probe(video)
    filters = [
        f"drawbox=x={r['box'][0]}:y={r['box'][1]}:w={r['box'][2] - r['box'][0]}:h={r['box'][3] - r['box'][1]}"
        f":color=white:t=fill:enable='between(t,{r['start']},{r['end']})'"
        for r in regions
    ] or ["null"]
    media.run(
        [
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", f"color=c=black:s={info['width']}x{info['height']}:r={info['fps']:.3f}:d={info['duration']:.3f}",
            "-vf", ",".join(filters), "-pix_fmt", "gray", "-c:v", "libx264", "-crf", "0", str(out),
        ]
    )
    return out


class FfmpegDelogoProvider:
    def remove_text(self, video: Path, regions: list[dict], out: Path, work_dir: Path) -> None:
        info = media.probe(video)
        w, h = info["width"], info["height"]
        filters = []
        for r in regions:
            x1, y1, x2, y2 = r["box"]
            # delogo は領域がフレーム端に接するとエラーになるので 1px 内側に収める
            x1, y1 = max(1, x1), max(1, y1)
            x2, y2 = min(w - 2, x2), min(h - 2, y2)
            if x2 - x1 < 4 or y2 - y1 < 4:
                continue
            filters.append(
                f"delogo=x={x1}:y={y1}:w={x2 - x1}:h={y2 - y1}:enable='between(t,{r['start']},{r['end']})'"
            )
        media.run(
            [
                "ffmpeg", "-y", "-i", str(video), "-vf", ",".join(filters) or "null",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
                "-c:a", "copy", str(out),
            ]
        )


class HttpInpaintProvider:
    """専用ツール API 用のひな形。

    想定しているやり取り (実際の API 仕様に合わせて書き換える):
        POST {endpoint}/jobs        multipart: video, mask(動画), regions(JSON)  -> {"job_id": "..."}
        GET  {endpoint}/jobs/{id}   -> {"status": "running"|"succeeded"|"failed", "result_url": "..."}
    認証情報は Secrets Manager (INPAINT_SECRET_ARN) の JSON {"endpoint": "...", "api_key": "..."}。
    """

    poll_interval = 5
    timeout_sec = 60 * 60

    def __init__(self) -> None:
        import boto3

        secret = boto3.client("secretsmanager").get_secret_value(SecretId=os.environ["INPAINT_SECRET_ARN"])
        conf = json.loads(secret["SecretString"])
        self.endpoint = conf["endpoint"].rstrip("/")
        self.headers = {"Authorization": f"Bearer {conf['api_key']}"}

    def remove_text(self, video: Path, regions: list[dict], out: Path, work_dir: Path) -> None:
        import requests

        mask = make_mask_video(video, regions, work_dir / "mask.mp4")
        with open(video, "rb") as v, open(mask, "rb") as m:
            resp = requests.post(
                f"{self.endpoint}/jobs",
                headers=self.headers,
                files={"video": ("video.mp4", v, "video/mp4"), "mask": ("mask.mp4", m, "video/mp4")},
                data={"regions": json.dumps(regions)},
                timeout=300,
            )
        resp.raise_for_status()
        job_id = resp.json()["job_id"]

        deadline = time.time() + self.timeout_sec
        while time.time() < deadline:
            st = requests.get(f"{self.endpoint}/jobs/{job_id}", headers=self.headers, timeout=60)
            st.raise_for_status()
            body = st.json()
            if body["status"] == "succeeded":
                with requests.get(body["result_url"], stream=True, timeout=300) as dl:
                    dl.raise_for_status()
                    with open(out, "wb") as f:
                        for chunk in dl.iter_content(1 << 20):
                            f.write(chunk)
                return
            if body["status"] == "failed":
                raise RuntimeError(f"inpaint API failed: {body}")
            time.sleep(self.poll_interval)
        raise TimeoutError(f"inpaint API job {job_id} timed out")


def _overlaps(a: tuple, b: tuple) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def plan_windows(regions: list[dict], fps: float, n_frames: int, size: tuple[int, int],
                 *, context_sec: float = 2.0, pad_px: int = 48, max_sec: float = 10.0) -> list[dict]:
    """テロップごとに ProPainter へ渡す範囲 (前後の文脈を含むフレーム範囲 + 周囲を含む切り抜き) を決める。

    動画全体を GPU に載せるとメモリが足りないため、テロップの周辺だけを切り出して処理する。
    同じ時間帯で切り抜きが重なるテロップ (2 行のテロップなど) は 1 つの範囲にまとめる
    (別々に処理して貼り戻すと、後から貼った方が先に消した文字を元に戻してしまうため)。
    テロップが続く区間が max_sec より長いときは、数秒ずつに分ける (core = 貼り戻すフレーム範囲)。
    切り抜きは ProPainter の制約に合わせて 8 の倍数にそろえる。
    """
    w, h = size
    ctx, max_core = int(context_sec * fps), max(1, int(max_sec * fps))
    groups = []
    for r in regions:
        x1, y1, x2, y2 = r["box"]
        groups.append({
            "ids": [r.get("id")],
            "frames": (max(0, int((r["start"] - context_sec) * fps)), min(n_frames, int((r["end"] + context_sec) * fps) + 1)),
            "area": (max(0, x1 - pad_px), max(0, y1 - pad_px), min(w, x2 + pad_px), min(h, y2 + pad_px)),
            "masks": [((x1, y1, x2, y2), (int(r["start"] * fps), int(r["end"] * fps) + 1))],
        })
    merged = True
    while merged:  # まとめた結果がさらに別の範囲と重なることがあるので、重ならなくなるまで繰り返す
        merged = False
        for i in range(len(groups)):
            for j in range(i + 1, len(groups)):
                g, o = groups[i], groups[j]
                ga, oa = g["area"], o["area"]
                if (_overlaps(g["frames"], o["frames"]) and _overlaps((ga[0], ga[2]), (oa[0], oa[2]))
                        and _overlaps((ga[1], ga[3]), (oa[1], oa[3]))):
                    groups[i] = {
                        "ids": g["ids"] + o["ids"],
                        "frames": (min(g["frames"][0], o["frames"][0]), max(g["frames"][1], o["frames"][1])),
                        "area": (min(ga[0], oa[0]), min(ga[1], oa[1]), max(ga[2], oa[2]), max(ga[3], oa[3])),
                        "masks": g["masks"] + o["masks"],
                    }
                    del groups[j]
                    merged = True
                    break
            if merged:
                break

    out = []
    for g in sorted(groups, key=lambda g: g["frames"][0]):
        cx1, cy1, cx2, cy2 = g["area"]
        cw, ch = (cx2 - cx1) // 8 * 8, (cy2 - cy1) // 8 * 8
        m0 = min(m[1][0] for m in g["masks"])
        m1 = min(n_frames, max(m[1][1] for m in g["masks"]))
        if cw < 16 or ch < 16 or m1 - m0 < 1:
            continue
        boxes = [m[0] for m in g["masks"]]
        for c0 in range(m0, m1, max_core):
            c1 = min(m1, c0 + max_core)
            f0, f1 = max(0, c0 - ctx), min(n_frames, c1 + ctx)
            if f1 - f0 < 2:
                continue
            out.append(
                {
                    "id": ",".join(str(i) for i in g["ids"]),
                    "frames": (f0, f1),
                    "core": (c0, c1),
                    "crop": (cx1, cy1, cx1 + cw, cy1 + ch),
                    "masks": g["masks"],
                    "mask_frames": (m0, m1),
                    "box": (min(b[0] for b in boxes), min(b[1] for b in boxes),
                            max(b[2] for b in boxes), max(b[3] for b in boxes)),
                }
            )
    return out


def inpaint_by_windows(video: Path, regions: list[dict], out: Path, work_dir: Path, run_window, *,
                       parallel: int = 1) -> None:
    """テロップ周辺の範囲ごとに ProPainter を実行し、結果を元のフレームに貼り戻して動画にする。

    run_window(job_dir, in_dir, mask_dir, fps) は ProPainter の結果フレーム (PNG のパス一覧) を返す。
    範囲どうしは重ならないので、parallel > 1 なら同時に実行してよい (外部 API 用)。
    """
    import shutil
    from concurrent.futures import ThreadPoolExecutor

    from PIL import Image, ImageDraw

    info = media.probe(video)
    frames_dir = work_dir / "frames"
    shutil.rmtree(frames_dir, ignore_errors=True)
    frames_dir.mkdir(parents=True)
    media.run(["ffmpeg", "-y", "-i", str(video), "-vsync", "0", str(frames_dir / "%06d.png")])
    files = sorted(frames_dir.glob("*.png"))
    fps = info["fps"]
    # 1 回に送る長さ (秒)。長いほど依頼回数は減るが、GPU メモリを多く使う
    windows = plan_windows(regions, fps, len(files), (info["width"], info["height"]),
                           max_sec=float(os.environ.get("INPAINT_WINDOW_SEC", "10")))

    def active(win: dict, idx: int) -> list[tuple]:
        """そのフレームで消す枠。マスクは前後の文脈部分にも付ける (分割の境目で消し方がそろうように)。"""
        return [box for box, (m0, m1) in win["masks"] if m0 <= idx < m1]

    def pasted(win: dict, idx: int) -> bool:
        c0, c1 = win["core"]
        return c0 <= idx < c1 and bool(active(win, idx))

    def prepare(i: int, win: dict) -> Path:
        f0, f1 = win["frames"]
        cx1, cy1, cx2, cy2 = win["crop"]
        job = work_dir / f"win{i:03d}"
        shutil.rmtree(job, ignore_errors=True)
        (job / "in").mkdir(parents=True)
        (job / "mask").mkdir()
        for k, f in enumerate(files[f0:f1]):
            Image.open(f).convert("RGB").crop((cx1, cy1, cx2, cy2)).save(job / "in" / f"{k:05d}.png")
            m = Image.new("L", (cx2 - cx1, cy2 - cy1), 0)
            for bx1, by1, bx2, by2 in active(win, f0 + k):
                ImageDraw.Draw(m).rectangle((bx1 - cx1, by1 - cy1, bx2 - cx1, by2 - cy1), fill=255)
            m.save(job / "mask" / f"{k:05d}.png")
        return job

    def process(item: tuple[int, dict]) -> tuple[dict, Path, list[Path]]:
        i, win = item
        job = prepare(i, win)
        return win, job, run_window(job, job / "in", job / "mask", fps)

    items = list(enumerate(windows))
    if parallel > 1:
        with ThreadPoolExecutor(max_workers=parallel) as pool:
            results = list(pool.map(process, items))
    else:
        results = [process(it) for it in items]

    # テロップが出ている間のフレームだけ、切り抜き部分を差し戻す
    for win, job, result in results:
        f0, f1 = win["frames"]
        cx1, cy1, cx2, cy2 = win["crop"]
        n = f1 - f0
        for k in range(n):
            if not pasted(win, f0 + k) or not result:
                continue
            rk = min(len(result) - 1, round(k * len(result) / n))  # 返ってきたフレーム数がずれても対応させる
            patch = Image.open(result[rk])
            if patch.size != (cx2 - cx1, cy2 - cy1):
                patch = patch.resize((cx2 - cx1, cy2 - cy1))
            frame = Image.open(files[f0 + k])
            frame.paste(patch.convert(frame.mode), (cx1, cy1))
            frame.save(files[f0 + k])
        shutil.rmtree(job, ignore_errors=True)

    media.run(
        ["ffmpeg", "-y", "-framerate", f"{fps:.6f}", "-i", str(frames_dir / "%06d.png"), "-i", str(video),
         "-map", "0:v", "-map", "1:a?", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
         "-pix_fmt", "yuv420p", "-c:a", "copy", "-shortest", str(out)]
    )


class ProPainterLocalProvider:
    """コンテナ内の ProPainter (/opt/ProPainter) を GPU で実行する。gpu-worker イメージで使う。"""

    root = Path(os.environ.get("PROPAINTER_ROOT", "/opt/ProPainter"))

    def remove_text(self, video: Path, regions: list[dict], out: Path, work_dir: Path) -> None:
        import subprocess
        import sys

        def run_window(job: Path, in_dir: Path, mask_dir: Path, fps: float) -> list[Path]:
            subprocess.run(
                [sys.executable, "inference_propainter.py", "-i", str(in_dir), "-m", str(mask_dir),
                 "-o", str(job / "out"), "--save_frames", "--fp16",
                 "--subvideo_length", os.environ.get("PROPAINTER_SUBVIDEO_LENGTH", "50")],
                cwd=self.root, check=True,
            )
            return sorted((job / "out" / "in" / "frames").glob("*.png"))

        inpaint_by_windows(video, regions, out, work_dir, run_window)


def select_output(output) -> str:
    """Replicate の出力 (URL 1 つ / 一覧 / 辞書) から、文字を消した動画の URL を選ぶ。

    ProPainter は「マスクを重ねた入力」と「補間後の動画 (inpaint_out)」の両方を返すことがあるので、
    名前に inpaint / out / result を含むものを優先し、mask / masked を含むものは避ける。
    """
    import logging

    if isinstance(output, str):
        urls = [output]
    elif isinstance(output, dict):
        urls = [v for v in output.values() if isinstance(v, str)]
    else:
        urls = [u for u in output if isinstance(u, str)]
    logging.getLogger("vt").info("replicate outputs: %s", urls)
    if not urls:
        raise RuntimeError(f"replicate の出力に動画がありません: {output!r}")

    def name(u: str) -> str:
        return u.rsplit("/", 1)[-1].split("?")[0].lower()

    for u in urls:
        if "inpaint" in name(u):
            return u
    rest = [u for u in urls if "mask" not in name(u)]
    return (rest or urls)[-1]


def check_filled(frames: list[Path], masks: list[Path], label: str = "") -> None:
    """消した部分が黒一色 (= 補間されていない) なら止める。黒い四角のまま公開しないため。"""
    import numpy as np
    from PIL import Image

    active = [i for i, m in enumerate(masks) if np.asarray(Image.open(m)).max() > 127]
    if not active or not frames:
        return
    i = active[len(active) // 2]
    k = min(len(frames) - 1, round(i * len(frames) / len(masks)))
    m = np.asarray(Image.open(masks[i])) > 127
    f = np.asarray(Image.open(frames[k]).convert("L").resize(m.shape[::-1]))
    region = f[m]
    if region.size and region.mean() < 20 and region.std() < 6:
        raise RuntimeError(
            f"replicate の結果 ({label}) で、消した部分が黒一色になっています (平均 {region.mean():.1f})。"
            "補間後の動画ではない出力を受け取っている可能性があります"
        )


class ReplicateProPainterProvider:
    """Replicate の ProPainter (jd7h/propainter) を呼ぶ。

    認証情報は Secrets Manager (INPAINT_SECRET_ARN) の JSON:
        {"api_token": "r8_...", "version": "<モデルのバージョンID>",
         "video_field": "video", "mask_field": "mask"}
    入力名はモデルの API ページ (https://replicate.com/jd7h/propainter/api) で確認して合わせる。
    動画とマスクは S3 の署名付き URL で渡す。
    """

    api = "https://api.replicate.com/v1/predictions"
    poll_interval = 5
    timeout_sec = 60 * 60

    def __init__(self) -> None:
        import boto3

        secret = boto3.client("secretsmanager").get_secret_value(SecretId=os.environ["INPAINT_SECRET_ARN"])
        self.conf = json.loads(secret["SecretString"])
        self.headers = {"Authorization": f"Bearer {self.conf['api_token']}"}

    def _presign(self, path: Path, key: str) -> str:
        import boto3

        s3, bucket = boto3.client("s3"), os.environ["BUCKET"]
        s3.upload_file(str(path), bucket, key)
        return s3.generate_presigned_url("get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=7200)

    def _predict(self, video_url: str, mask_url: str) -> str:
        """予測を作って終わるまで待ち、結果動画の URL を返す。"""
        import requests

        inputs = {self.conf.get("video_field", "video"): video_url, self.conf.get("mask_field", "mask"): mask_url}
        body = {"version": self.conf["version"], "input": inputs}
        for attempt in range(30):
            resp = requests.post(self.api, headers=self.headers, json=body, timeout=60)
            if resp.status_code != 429:
                break
            # 回数制限 (残高 $5 未満だと 1 分 6 回・同時 1 件)。言われた秒数だけ待って送り直す
            try:
                wait = float(resp.json().get("retry_after", 10))
            except ValueError:
                wait = 10.0
            time.sleep(min(60.0, wait + 1 + attempt))
        if not resp.ok:
            # 402 (残高・支払い) や 422 (入力・バージョン違い) の理由は本文に入っているので、エラーに含める
            raise RuntimeError(f"replicate {resp.status_code}: {resp.text[:500]}")
        pred = resp.json()
        deadline = time.time() + self.timeout_sec
        while pred["status"] not in ("succeeded", "failed", "canceled"):
            if time.time() > deadline:
                raise TimeoutError(f"replicate prediction {pred['id']} timed out")
            time.sleep(self.poll_interval)
            pred = requests.get(pred["urls"]["get"], headers=self.headers, timeout=60).json()
        if pred["status"] != "succeeded":
            raise RuntimeError(f"replicate prediction {pred['status']}: {pred.get('error')}")
        return select_output(pred["output"])

    def remove_text(self, video: Path, regions: list[dict], out: Path, work_dir: Path) -> None:
        """動画全体を送ると GPU のメモリが足りないので、テロップ周辺の短い切り抜き動画ごとに送る。"""
        import requests

        prefix = f"jobs/{os.environ.get('JOB_ID', 'local')}/inpaint"

        def run_window(job: Path, in_dir: Path, mask_dir: Path, fps: float) -> list[Path]:
            clip, mask = job / "clip.mp4", job / "mask.mp4"
            media.run(["ffmpeg", "-y", "-framerate", f"{fps:.6f}", "-i", str(in_dir / "%05d.png"),
                       "-c:v", "libx264", "-crf", "12", "-pix_fmt", "yuv420p", str(clip)])
            media.run(["ffmpeg", "-y", "-framerate", f"{fps:.6f}", "-i", str(mask_dir / "%05d.png"),
                       "-c:v", "libx264", "-crf", "0", "-pix_fmt", "gray", str(mask)])
            url = self._predict(self._presign(clip, f"{prefix}/{job.name}/video.mp4"),
                                self._presign(mask, f"{prefix}/{job.name}/mask.mp4"))
            result = job / "result.mp4"
            with requests.get(url, stream=True, timeout=300) as dl:
                dl.raise_for_status()
                with open(result, "wb") as f:
                    for chunk in dl.iter_content(1 << 20):
                        f.write(chunk)
            self._presign(result, f"{prefix}/{job.name}/result.mp4")  # 確認用に S3 へ残す
            (job / "res").mkdir(exist_ok=True)
            media.run(["ffmpeg", "-y", "-i", str(result), "-vsync", "0", str(job / "res" / "%05d.png")])
            frames = sorted((job / "res").glob("*.png"))
            check_filled(frames, sorted(mask_dir.glob("*.png")), job.name)
            return frames

        inpaint_by_windows(video, regions, out, work_dir, run_window,
                           parallel=int(os.environ.get("REPLICATE_PARALLEL", "4")))


def get_provider(name: str | None = None) -> InpaintProvider:
    name = name or os.environ.get("INPAINT_PROVIDER", "ffmpeg_delogo")
    if name == "ffmpeg_delogo":
        return FfmpegDelogoProvider()
    if name == "propainter_local":
        return ProPainterLocalProvider()
    if name == "replicate":
        return ReplicateProPainterProvider()
    if name == "http":
        return HttpInpaintProvider()
    raise ValueError(f"unknown INPAINT_PROVIDER: {name}")
