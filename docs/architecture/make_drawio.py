"""draw.io 版の構成図を生成する (draw.io 標準の AWS アイコン mxgraph.aws4.* を使用)。

    python docs/architecture/make_drawio.py   ->  architecture.drawio
draw.io (app.diagrams.net / VS Code 拡張) で開いて編集できます。
"""

from pathlib import Path
from xml.sax.saxutils import escape

OUT = Path(__file__).with_name("architecture.drawio")

# AWS カテゴリ色 (アイコンの背景色)
COMPUTE, STORAGE, INTEG, ML, SEC, DB, MGMT = "#ED7100", "#7AA116", "#E7157B", "#01A88D", "#DD344C", "#C925D1", "#E7157B"

cells: list[str] = []
_id = 10


def nid() -> str:
    global _id
    _id += 1
    return f"n{_id}"


def icon(label: str, res: str, color: str, x: int, y: int, parent: str = "1") -> str:
    i = nid()
    style = (
        "sketch=0;outlineConnect=0;fontColor=#232F3E;fillColor=" + color + ";strokeColor=#ffffff;"
        "dashed=0;verticalLabelPosition=bottom;verticalAlign=top;align=center;html=1;fontSize=12;"
        f"fontStyle=0;aspect=fixed;shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.{res};"
    )
    cells.append(
        f'<mxCell id="{i}" value="{escape(label)}" style="{style}" vertex="1" parent="{parent}">'
        f'<mxGeometry x="{x}" y="{y}" width="64" height="64" as="geometry"/></mxCell>'
    )
    return i


def plain_icon(label: str, shape: str, x: int, y: int) -> str:
    i = nid()
    style = (
        "sketch=0;outlineConnect=0;fontColor=#232F3E;fillColor=#232F3E;strokeColor=none;dashed=0;"
        "verticalLabelPosition=bottom;verticalAlign=top;align=center;html=1;fontSize=12;aspect=fixed;"
        f"shape=mxgraph.aws4.{shape};"
    )
    cells.append(
        f'<mxCell id="{i}" value="{escape(label)}" style="{style}" vertex="1" parent="1">'
        f'<mxGeometry x="{x}" y="{y}" width="64" height="64" as="geometry"/></mxCell>'
    )
    return i


def group(label: str, x: int, y: int, w: int, h: int, style: str) -> str:
    i = nid()
    cells.append(
        f'<mxCell id="{i}" value="{escape(label)}" style="{style}" vertex="1" parent="1">'
        f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>'
    )
    return i


def edge(src: str, dst: str, label: str = "", color: str = "#545B64", dashed: bool = False) -> None:
    i = nid()
    style = (
        f"edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;endArrow=block;endFill=1;strokeColor={color};"
        f"fontSize=11;fontColor={color};" + ("dashed=1;" if dashed else "")
    )
    cells.append(
        f'<mxCell id="{i}" value="{escape(label)}" style="{style}" edge="1" parent="1" source="{src}" target="{dst}">'
        '<mxGeometry relative="1" as="geometry"/></mxCell>'
    )


AWS_CLOUD = (
    "points=[];outlineConnect=0;gradientColor=none;html=1;whiteSpace=wrap;fontSize=12;fontStyle=0;"
    "container=1;pointerEvents=0;collapsible=0;recursiveResize=0;shape=mxgraph.aws4.group;"
    "grIcon=mxgraph.aws4.group_aws_cloud_alt;strokeColor=#232F3E;fillColor=none;verticalAlign=top;"
    "align=left;spacingLeft=30;fontColor=#232F3E;dashed=0;"
)
BOX = "rounded=1;arcSize=3;html=1;fillColor={f};strokeColor={s};verticalAlign=top;align=left;spacingLeft=10;fontSize=12;dashed={d};"

# ---- groups ----
group("AWS Cloud", 160, -20, 1500, 700, AWS_CLOUD)
group("Step Functions ワークフロー / ECS on Fargate ワーカー", 440, 90, 980, 300, BOX.format(f="#FFF8F0", s="#ED7100", d=1))
group("保存・状態", 1450, 90, 180, 560, BOX.format(f="#F2FBF4", s="#3F8624", d=0))

# ---- nodes ----
user = plain_icon("利用者", "user", 40, 300)
s3_in = icon("S3\ninput/*.mp4", "simple_storage_service", STORAGE, 200, 300)
eb = icon("EventBridge\nObject Created", "eventbridge", INTEG, 300, 300)
sfn = icon("Step Functions", "step_functions", INTEG, 300, 450)

y1, y2 = 140, 280
extract = icon("① フレーム抽出\n(ffmpeg)", "fargate", COMPUTE, 480, y1)
ocr = icon("② 日本語OCR\n(PaddleOCR)", "fargate", COMPUTE, 640, y1)
translate = icon("③ OCR補正+英訳\n(食材ルール適用)", "fargate", COMPUTE, 800, y1)
inpaint = icon("④ 日本語テロップ除去", "fargate", COMPUTE, 960, y1)
compose = icon("⑤ 英語テキスト合成\n(ffmpeg+Pillow)", "fargate", COMPUTE, 960, y2)
qa = icon("⑥ エージェント確認", "fargate", COMPUTE, 800, y2)
publish = icon("⑦ 保存", "fargate", COMPUTE, 1300, y2)

bedrock = icon("Amazon Bedrock\n(Claude)", "bedrock", ML, 800, 470)
secrets = icon("Secrets Manager\nAPIキー", "secrets_manager", SEC, 1120, 470)
sns = icon("SNS\n完了・失敗通知", "sns", INTEG, 300, 580)
logs = icon("CloudWatch Logs", "cloudwatch_2", MGMT, 480, 470)
ecr = icon("ECR\nワーカーイメージ", "ecr", COMPUTE, 640, 470)

s3_jobs = icon("S3 jobs/{id}/\nocr.json ほか", "simple_storage_service", STORAGE, 1508, 140)
ddb = icon("DynamoDB\nジョブ状態", "dynamodb", DB, 1508, 330)
s3_out = icon("S3 output/\n(NG時 review/)", "simple_storage_service", STORAGE, 1508, 520)

rules = icon("S3 rules/\n食材ルールリスト", "simple_storage_service", STORAGE, 800, 0)
api = plain_icon("文字除去 / インペイント API\n(外部サービス)", "internet_alt1", 1720, 140)

# ---- edges ----
edge(user, s3_in, "1分動画をアップロード")
edge(s3_in, eb)
edge(eb, sfn, "起動")
edge(sfn, extract, "RunTask")
edge(extract, ocr)
edge(ocr, translate)
edge(translate, inpaint)
edge(inpaint, compose)
edge(compose, qa)
edge(qa, publish, "OK", color="#3F8624")
edge(qa, translate, "NG: 再翻訳 (最大2回)", color="#D13212", dashed=True)
edge(rules, translate, "代用品ルールを参照", color="#7AA116")
edge(translate, bedrock, dashed=True)
edge(qa, bedrock, "フレーム画像で確認", dashed=True)
edge(inpaint, api, "HTTPS", color="#ED7100")
edge(inpaint, secrets, dashed=True)
edge(ocr, s3_jobs, "日本語テキスト保存", dashed=True)
edge(publish, ddb, dashed=True)
edge(publish, s3_out)
edge(sfn, sns, "結果", dashed=True)
edge(sns, user, "メール等")

xml = (
    '<mxfile host="app.diagrams.net"><diagram id="vt" name="動画テロップ翻訳パイプライン">'
    '<mxGraphModel dx="1800" dy="800" grid="1" gridSize="10" guides="1" tooltips="1" connect="1" arrows="1" '
    'fold="1" page="1" pageScale="1" pageWidth="1900" pageHeight="760" math="0" shadow="0"><root>'
    '<mxCell id="0"/><mxCell id="1" parent="0"/>' + "".join(cells) + "</root></mxGraphModel></diagram></mxfile>"
)
OUT.write_text(xml.replace("\n", "&#xa;"), encoding="utf-8")
print(f"wrote {OUT}")
