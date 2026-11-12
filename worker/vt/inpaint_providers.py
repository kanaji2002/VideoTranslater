"""文字除去 / インペイントの実装切り替え。

INPAINT_PROVIDER で選ぶ:
- ffmpeg_delogo : 外部 API なしで動く簡易版 (delogo フィルタで周囲から補間)。動作確認用。
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

    def remove_text(self, video: Path, regions: list[dict], out: Path, work_dir: Path) -> None:
        import requests

        mask = make_mask_video(video, regions, work_dir / "mask.mp4")
        prefix = f"jobs/{os.environ.get('JOB_ID', 'local')}/inpaint"
        inputs = {
            self.conf.get("video_field", "video"): self._presign(video, f"{prefix}/video.mp4"),
            self.conf.get("mask_field", "mask"): self._presign(mask, f"{prefix}/mask.mp4"),
        }
        resp = requests.post(self.api, headers=self.headers, json={"version": self.conf["version"], "input": inputs}, timeout=60)
        resp.raise_for_status()
        pred = resp.json()
        deadline = time.time() + self.timeout_sec
        while pred["status"] not in ("succeeded", "failed", "canceled"):
            if time.time() > deadline:
                raise TimeoutError(f"replicate prediction {pred['id']} timed out")
            time.sleep(self.poll_interval)
            pred = requests.get(pred["urls"]["get"], headers=self.headers, timeout=60).json()
        if pred["status"] != "succeeded":
            raise RuntimeError(f"replicate prediction {pred['status']}: {pred.get('error')}")
        output = pred["output"]
        url = output if isinstance(output, str) else output[-1]
        with requests.get(url, stream=True, timeout=300) as dl:
            dl.raise_for_status()
            with open(out, "wb") as f:
                for chunk in dl.iter_content(1 << 20):
                    f.write(chunk)
        # ProPainter は音声を返さないことがあるので、元動画の音声を戻す
        tmp = out.with_suffix(".noaudio.mp4")
        out.rename(tmp)
        media.run(["ffmpeg", "-y", "-i", str(tmp), "-i", str(video), "-map", "0:v", "-map", "1:a?",
                   "-c:v", "copy", "-c:a", "copy", "-shortest", str(out)])


def get_provider(name: str | None = None) -> InpaintProvider:
    name = name or os.environ.get("INPAINT_PROVIDER", "ffmpeg_delogo")
    if name == "ffmpeg_delogo":
        return FfmpegDelogoProvider()
    if name == "replicate":
        return ReplicateProPainterProvider()
    if name == "http":
        return HttpInpaintProvider()
    raise ValueError(f"unknown INPAINT_PROVIDER: {name}")
