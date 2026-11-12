"""ECS ワーカーのエントリポイント。

STEP 環境変数で実行する工程を選び、結果を Step Functions のタスクトークンで返す。
ローカル実行時 (TASK_TOKEN なし) は結果を標準出力に出す。
"""

from __future__ import annotations

import json
import logging
import os
import sys
import traceback

from .context import JobContext
from .steps import compose, extract, inpaint, ocr, publish, qa, translate

STEPS = {
    "extract": extract.run,
    "ocr": ocr.run,
    "translate": translate.run,
    "inpaint": inpaint.run,
    "compose": compose.run,
    "qa": qa.run,
    "publish": publish.run,
}

log = logging.getLogger("vt")


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    step = argv[1] if len(argv) > 1 else os.environ.get("STEP", "")
    if step not in STEPS:
        print(f"unknown step: {step!r} (choose from {', '.join(STEPS)})", file=sys.stderr)
        return 2

    ctx = JobContext.from_env()
    token = os.environ.get("TASK_TOKEN")
    sfn = None
    if token:
        import boto3

        sfn = boto3.client("stepfunctions")

    log.info("job=%s step=%s attempt=%s", ctx.job_id, step, ctx.attempt)
    try:
        ctx.set_status(f"running:{step}", attempt=ctx.attempt)
        result = STEPS[step](ctx) or {}
    except Exception as e:  # noqa: BLE001 - 失敗は Step Functions に返して Catch させる
        log.exception("step %s failed", step)
        if sfn:
            sfn.send_task_failure(
                taskToken=token,
                error=type(e).__name__[:256],
                cause=traceback.format_exc()[-32000:],
            )
            return 0
        return 1

    if sfn:
        sfn.send_task_success(taskToken=token, output=json.dumps(result, ensure_ascii=False))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
