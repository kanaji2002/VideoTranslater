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


def edge(src: str, dst: str, label: str = "", color: str = "#545B64", dashed: bool = False,
         exit: tuple[float, float] | None = None, entry: tuple[float, float] | None = None,
         points: list[tuple[int, int]] | None = None) -> None:
    """exit / entry は接続位置 (0〜1)、points は折れ曲がり位置。線の交差を避けるために指定する。"""
    i = nid()
    style = (
        f"edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;endArrow=block;endFill=1;strokeColor={color};"
        f"fontSize=11;fontColor={color};labelBackgroundColor=#FFFFFF;" + ("dashed=1;" if dashed else "")
    )
    if exit:
        style += f"exitX={exit[0]};exitY={exit[1]};exitDx=0;exitDy=0;"
    if entry:
        style += f"entryX={entry[0]};entryY={entry[1]};entryDx=0;entryDy=0;"
    pts = ""
    if points:
        pts = '<Array as="points">' + "".join(f'<mxPoint x="{x}" y="{y}"/>' for x, y in points) + "</Array>"
    cells.append(
        f'<mxCell id="{i}" value="{escape(label)}" style="{style}" edge="1" parent="1" source="{src}" target="{dst}">'
        f'<mxGeometry relative="1" as="geometry">{pts}</mxGeometry></mxCell>'
    )


AWS_CLOUD = (
    "points=[];outlineConnect=0;gradientColor=none;html=1;whiteSpace=wrap;fontSize=12;fontStyle=0;"
    "container=1;pointerEvents=0;collapsible=0;recursiveResize=0;shape=mxgraph.aws4.group;"
    "grIcon=mxgraph.aws4.group_aws_cloud_alt;strokeColor=#232F3E;fillColor=none;verticalAlign=top;"
    "align=left;spacingLeft=30;fontColor=#232F3E;dashed=0;"
)
BOX = "rounded=1;arcSize=3;html=1;fillColor={f};strokeColor={s};verticalAlign=top;align=left;spacingLeft=10;fontSize=12;dashed={d};"

def note(text: str, x: int, y: int, w: int, h: int) -> str:
    i = nid()
    style = "text;html=1;whiteSpace=wrap;fillColor=#F7F7F7;strokeColor=#D5DBDB;spacing=8;align=left;verticalAlign=top;fontSize=12;fontColor=#232F3E;"
    cells.append(
        f'<mxCell id="{i}" value="{escape(text)}" style="{style}" vertex="1" parent="1">'
        f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>'
    )
    return i


# ---- レイアウト ----
# 工程①〜⑦ を左から右へ 1 列に並べ、AI (Bedrock) と再翻訳ループは上、つなぎ先 (API・ルール・ログ) は下に置く。
ROW = 230                      # 工程アイコンの y
X = {n: 560 + 150 * (n - 1) for n in range(1, 8)}  # 工程 n のアイコンの x

group("AWS Cloud (us-east-1)", 150, -60, 1640, 660, AWS_CLOUD)
fargate = group("ECS on Fargate ワーカー (1つのイメージを STEP で切替)", 530, 150, 1020, 220, BOX.format(f="#FFF8F0", s="#ED7100", d=1))
group("AWS Batch GPU (g6/g5・待機中は0台)\nGPU 枠の承認後、④ をここで実行\n(ECR の GPU 用イメージ)", 1220, 430, 250, 110,
      BOX.format(f="#FFFFFF", s="#ED7100", d=1))
group("保存先", 1600, -10, 160, 580, BOX.format(f="#F2FBF4", s="#3F8624", d=0))

# ---- 入口 ----
user = plain_icon("利用者", "user", 40, ROW)
s3_in = icon("S3\ninput/*.mp4", "simple_storage_service", STORAGE, 190, ROW)
eb = icon("EventBridge", "eventbridge", INTEG, 300, ROW)
sfn = icon("Step Functions\nワークフロー", "step_functions", INTEG, 410, ROW)
sns = icon("SNS\n完了・失敗通知", "sns", INTEG, 410, 430)

# ---- 工程 ----
s1 = icon("① フレーム抽出\n(ffmpeg)", "fargate", COMPUTE, X[1], ROW)
s2 = icon("② 日本語OCR\n(PaddleOCR)", "fargate", COMPUTE, X[2], ROW)
s3 = icon("③ OCR補正+英訳\n(食材ルール適用)", "fargate", COMPUTE, X[3], ROW)
s4 = icon("④ テロップ除去\n(ProPainter)", "fargate", COMPUTE, X[4], ROW)
s5 = icon("⑤ 英語テロップ合成\n(ffmpeg+Pillow)", "fargate", COMPUTE, X[5], ROW)
s6 = icon("⑥ 確認エージェント\n(完成フレームを判定)", "fargate", COMPUTE, X[6], ROW)
s7 = icon("⑦ 保存", "fargate", COMPUTE, X[7], ROW)

# ---- 上: AI ----
bedrock = icon("Amazon Bedrock\nNova 2 Lite (③・⑥ で使用)\n※Claude は承認後に戻す", "bedrock", ML, 1085, -20)

# ---- 下: つなぎ先 ----
logs = icon("CloudWatch Logs", "cloudwatch_2", MGMT, 560, 430)
ecr = icon("ECR\nコンテナイメージ", "ecr", COMPUTE, 710, 430)
rules = icon("S3 rules/\n食材ルールリスト", "simple_storage_service", STORAGE, X[3], 430)
secrets = icon("Secrets Manager\nReplicate APIキー", "secrets_manager", SEC, X[4] + 110, 430)

# ---- 右: 保存先 ----
s3_jobs = icon("S3 jobs/{id}/\n途中ファイル", "simple_storage_service", STORAGE, 1648, 30)
s3_out = icon("S3 output/\n(NG時は review/)", "simple_storage_service", STORAGE, 1648, ROW)
ddb = icon("DynamoDB\nジョブ状態", "dynamodb", DB, 1648, 430)

# ---- AWS の外 ----
replicate = plain_icon("Replicate (外部API)\nProPainter を GPU で実行\n※現在はこちら", "internet_alt1", X[4], 650)
gha = group("GitHub Actions\nイメージのビルド + cdk deploy", 650, 650, 190, 60,
            "rounded=1;html=1;whiteSpace=wrap;fillColor=#24292F;strokeColor=none;fontColor=#FFFFFF;fontSize=12;align=center;verticalAlign=middle;")

note(
    "<b>使っている AI・エージェント</b><br>"
    "・③ OCR補正+英訳: Amazon Nova 2 Lite (Bedrock)。切り抜き画像と食材ルールを渡す<br>"
    "・⑥ 確認エージェント: Amazon Nova 2 Lite (Bedrock)。完成フレームを見て合否を返し、NG なら ③ からやり直し (最大2回)<br>"
    "・② OCR: PaddleOCR (日本語モデル、コンテナ内)<br>"
    "・④ 文字消し: ProPainter。現在は Replicate、GPU 枠の承認後は AWS Batch<br>"
    "切り替えはすべて infra/cdk.json (llmProvider / converseModelId / inpaintProvider)",
    1150, 640, 640, 110,
)

# ---- 線 ----
cy = ROW + 32  # アイコン中央の高さ
edge(user, s3_in, "アップロード")
edge(s3_in, eb)
edge(eb, sfn, "起動")
edge(sfn, s1, "RunTask")
edge(s1, s2)
edge(s2, s3)
edge(s3, s4)
edge(s4, s5)
edge(s5, s6)
edge(s6, s7, "OK", color="#3F8624")
# 再翻訳ループ (上を通す)
edge(s6, s3, "NG: 再翻訳 (最大2回)", color="#D13212", dashed=True,
     exit=(0.3, 0), entry=(0.8, 0), points=[(X[6] + 19, 180), (X[3] + 51, 180)])
# AI 呼び出し (上の Bedrock へ)
edge(s3, bedrock, "補正・英訳", color="#01A88D", dashed=True, exit=(0.2, 0), entry=(0, 0.5),
     points=[(X[3] + 13, 12)])
edge(s6, bedrock, "フレームで確認", color="#01A88D", dashed=True, exit=(0.8, 0), entry=(1, 0.5),
     points=[(X[6] + 51, 12)])
# 下のつなぎ先
edge(rules, s3, "ルール参照", color="#7AA116")
edge(s4, replicate, "HTTPS", color="#ED7100", exit=(0.3, 1), entry=(0.3, 0))
edge(s4, secrets, color="#DD344C", dashed=True, exit=(0.8, 1), entry=(0.5, 0),
     points=[(X[4] + 51, 400), (X[4] + 142, 400)])
# 保存
edge(s7, s3_out)
edge(s7, ddb, dashed=True, exit=(0.5, 1), entry=(0, 0.5), points=[(X[7] + 32, 462)])
edge(s2, s3_jobs, "OCR結果など", dashed=True, color="#7AA116", exit=(0.5, 0), entry=(0, 0.5),
     points=[(X[2] + 32, -35), (1620, -35), (1620, 62)])
# 通知・運用
edge(sfn, sns, "結果", dashed=True)
edge(sns, user, "メール等", exit=(0, 0.5), entry=(0.5, 1), points=[(72, 462)])
edge(gha, ecr, "push", color="#232F3E")
edge(ecr, fargate, "pull", color="#AAB7B8", dashed=True, exit=(0.5, 0), entry=(0.18, 1))
edge(s1, logs, "ログ", color="#AAB7B8", dashed=True, exit=(0.5, 1), entry=(0.5, 0))

xml = (
    '<mxfile host="app.diagrams.net"><diagram id="vt" name="動画テロップ翻訳パイプライン">'
    '<mxGraphModel dx="1800" dy="800" grid="1" gridSize="10" guides="1" tooltips="1" connect="1" arrows="1" '
    'fold="1" page="1" pageScale="1" pageWidth="1850" pageHeight="800" math="0" shadow="0"><root>'
    '<mxCell id="0"/><mxCell id="1" parent="0"/>' + "".join(cells) + "</root></mxGraphModel></diagram></mxfile>"
)
OUT.write_text(xml.replace("\n", "&#xa;"), encoding="utf-8")
print(f"wrote {OUT}")
