"""ジョブの入出力と状態管理。

S3 モード (本番) とローカルモード (LOCAL_ROOT を指定) の両方に対応する。
ジョブの成果物は jobs/{job_id}/ 以下に置く:

    jobs/{job_id}/source.mp4          元動画
    jobs/{job_id}/frames/*.jpg        サンプリングしたフレーム
    jobs/{job_id}/frames.json         フレームの時刻一覧
    jobs/{job_id}/ocr.json            日本語テロップ (文字・位置・時間)
    jobs/{job_id}/translation.json    英訳
    jobs/{job_id}/clean.mp4           文字除去済み動画
    jobs/{job_id}/output.mp4          英語合成済み動画
    jobs/{job_id}/qa.json             エージェント確認結果
"""

from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class JobContext:
    job_id: str
    input_key: str
    attempt: int = 0
    bucket: str | None = None
    local_root: Path | None = None
    work_dir: Path = field(default_factory=lambda: Path(os.environ.get("WORK_DIR", "/tmp/vt")))
    jobs_table: str | None = None

    @classmethod
    def from_env(cls) -> "JobContext":
        local_root = os.environ.get("LOCAL_ROOT")
        return cls(
            job_id=os.environ["JOB_ID"],
            input_key=os.environ["INPUT_KEY"],
            attempt=int(os.environ.get("ATTEMPT", "0") or 0),
            bucket=os.environ.get("BUCKET"),
            local_root=Path(local_root) if local_root else None,
            jobs_table=os.environ.get("JOBS_TABLE"),
        )

    # ---------- paths ----------
    def key(self, name: str) -> str:
        return f"jobs/{self.job_id}/{name}"

    def local(self, name: str) -> Path:
        p = self.work_dir / self.job_id / name
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    # ---------- storage ----------
    @property
    def _s3(self):
        import boto3

        return boto3.client("s3")

    def exists(self, key: str) -> bool:
        if self.local_root:
            return (self.local_root / key).exists()
        from botocore.exceptions import ClientError

        try:
            self._s3.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError:
            return False

    def download(self, key: str, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if self.local_root:
            shutil.copyfile(self.local_root / key, dest)
        else:
            self._s3.download_file(self.bucket, key, str(dest))
        return dest

    def upload(self, src: Path, key: str, content_type: str | None = None) -> str:
        if self.local_root:
            dst = self.local_root / key
            dst.parent.mkdir(parents=True, exist_ok=True)
            if Path(src).resolve() != dst.resolve():
                shutil.copyfile(src, dst)
        else:
            extra = {"ContentType": content_type} if content_type else None
            self._s3.upload_file(str(src), self.bucket, key, ExtraArgs=extra)
        return key

    def copy(self, src_key: str, dst_key: str) -> None:
        if self.local_root:
            dst = self.local_root / dst_key
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(self.local_root / src_key, dst)
        else:
            self._s3.copy_object(
                Bucket=self.bucket, Key=dst_key, CopySource={"Bucket": self.bucket, "Key": src_key}
            )

    def read_json(self, name: str) -> Any:
        path = self.download(self.key(name), self.local(name))
        return json.loads(path.read_text(encoding="utf-8"))

    def write_json(self, name: str, data: Any) -> str:
        path = self.local(name)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return self.upload(path, self.key(name), "application/json")

    def fetch(self, name: str) -> Path:
        """jobs/{job_id}/{name} をローカル作業ディレクトリに取得する (取得済みなら再利用)。"""
        path = self.local(name)
        if not path.exists():
            self.download(self.key(name), path)
        return path

    # ---------- status ----------
    def set_status(self, status: str, **attrs: Any) -> None:
        if not self.jobs_table:
            return
        import boto3

        names = {"#status": "status", "#updated": "updated_at", "#input": "input_key"}
        values: dict[str, Any] = {":status": status, ":updated": int(time.time()), ":input": self.input_key}
        sets = ["#status = :status", "#updated = :updated", "#input = :input"]
        for i, (k, v) in enumerate(attrs.items()):
            names[f"#a{i}"] = k
            values[f":a{i}"] = v
            sets.append(f"#a{i} = :a{i}")
        boto3.resource("dynamodb").Table(self.jobs_table).update_item(
            Key={"job_id": self.job_id},
            UpdateExpression="SET " + ", ".join(sets),
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
        )
