"""Amazon Bedrock 上の Claude 呼び出し (Anthropic SDK の Bedrock Mantle クライアント)。"""

from __future__ import annotations

import base64
import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any


class LLMRefusal(RuntimeError):
    pass


@lru_cache(maxsize=1)
def _client():
    from anthropic import AnthropicBedrockMantle

    return AnthropicBedrockMantle(aws_region=os.environ.get("BEDROCK_REGION", "us-east-1"))


def model_id() -> str:
    return os.environ.get("BEDROCK_MODEL_ID", "anthropic.claude-opus-5-5")


def managed_prompt(env_name: str, default: str) -> str:
    """Bedrock プロンプト管理のプロンプトを使う (環境変数に "ID" か "ID:バージョン" があれば)。

    未設定なら default (コード内のプロンプト) を使う。コンソールで文面を直せば再デプロイ不要。
    """
    ref = os.environ.get(env_name, "").strip()
    if not ref:
        return default
    import boto3

    pid, _, version = ref.partition(":") if not ref.startswith("arn:") else (ref, "", "")
    kwargs = {"promptIdentifier": pid, **({"promptVersion": version} if version else {})}
    prompt = boto3.client("bedrock-agent", region_name=os.environ.get("BEDROCK_REGION")).get_prompt(**kwargs)
    return prompt["variants"][0]["templateConfiguration"]["text"]["text"]


def image_block(path: Path) -> dict:
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/jpeg",
            "data": base64.standard_b64encode(Path(path).read_bytes()).decode(),
        },
    }


def ask_json(
    system: str,
    content: list[dict],
    schema: dict,
    *,
    effort: str = "medium",
    max_tokens: int = 16000,
) -> Any:
    """構造化出力 (JSON Schema) で Claude に問い合わせ、パース済みの結果を返す。"""
    response = _client().messages.create(
        model=model_id(),
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": content}],
        thinking={"type": "adaptive"},
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
    )
    if response.stop_reason == "refusal":
        raise LLMRefusal(str(getattr(response, "stop_details", None)))
    if response.stop_reason == "max_tokens":
        raise RuntimeError("LLM の出力が max_tokens で途切れました")
    text = next(b.text for b in response.content if b.type == "text")
    return json.loads(text)
