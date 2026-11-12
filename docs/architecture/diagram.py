"""構成図を生成する (AWS 公式アーキテクチャアイコンを同梱した `diagrams` ライブラリを使用)。

    pip install diagrams   # graphviz が必要 (apt install graphviz / brew install graphviz)
    python docs/architecture/diagram.py
"""

from pathlib import Path

from diagrams import Cluster, Diagram, Edge
from diagrams.aws.compute import ECR, Batch, Fargate
from diagrams.aws.database import Dynamodb
from diagrams.aws.general import User
from diagrams.onprem.ci import GithubActions
from diagrams.aws.integration import Eventbridge, SimpleNotificationServiceSns, StepFunctions
from diagrams.aws.management import CloudwatchLogs
from diagrams.aws.ml import Bedrock
from diagrams.aws.storage import SimpleStorageServiceS3Bucket

OUT = Path(__file__).with_name("architecture")
FONT = "Noto Sans CJK JP"

graph_attr = {
    "fontname": FONT,
    "fontsize": "22",
    "pad": "0.6",
    "nodesep": "0.7",
    "ranksep": "0.9",
    "splines": "spline",
    "bgcolor": "white",
}
node_attr = {"fontname": FONT, "fontsize": "13"}
edge_attr = {"fontname": FONT, "fontsize": "11", "color": "#545B64"}

with Diagram(
    "動画テロップ翻訳パイプライン",
    filename=str(OUT),
    outformat=["png", "svg"],
    direction="LR",
    show=False,
    graph_attr=graph_attr,
    node_attr=node_attr,
    edge_attr=edge_attr,
):
    user = User("利用者")
    gha = GithubActions("GitHub Actions\nイメージのビルド\n+ cdk deploy")

    with Cluster("AWS Cloud", graph_attr={"fontname": FONT, "bgcolor": "#F7F9FB", "pencolor": "#232F3E"}):
        s3_in = SimpleStorageServiceS3Bucket("S3\ninput/*.mp4")
        rule = Eventbridge("EventBridge\nObject Created")
        sfn = StepFunctions("Step Functions\nワークフロー")

        with Cluster(
            "ECS on Fargate ワーカー (1つのコンテナイメージ / STEP で切替)",
            graph_attr={"fontname": FONT, "bgcolor": "#FFF8F0", "pencolor": "#ED7100"},
        ):
            extract = Fargate("① フレーム抽出\nffmpeg")
            ocr = Fargate("② 日本語OCR\nPaddleOCR")
            translate = Fargate("③ OCR補正+英訳\n(食材ルール適用)")
            compose = Fargate("⑤ 英語テキスト合成\nffmpeg + Pillow")
            qa = Fargate("⑥ エージェント確認")
            publish = Fargate("⑦ 保存")

        with Cluster(
            "AWS Batch GPU (g6/g5, 待機中は0台)",
            graph_attr={"fontname": FONT, "bgcolor": "#FFF8F0", "pencolor": "#ED7100"},
        ):
            inpaint = Batch("④ 日本語テロップ除去\nProPainter")

        bedrock = Bedrock("Amazon Bedrock\nClaude")
        rules = SimpleStorageServiceS3Bucket("S3 rules/\n食材ルールリスト\n例: みりん → mirin\n(or sweet sherry)")

        with Cluster("保存・状態", graph_attr={"fontname": FONT, "bgcolor": "#F2FBF4", "pencolor": "#3F8624"}):
            s3_jobs = SimpleStorageServiceS3Bucket("S3 jobs/{id}/\nフレーム・ocr.json\ntranslation.json")
            s3_out = SimpleStorageServiceS3Bucket("S3 output/\n(NG時 review/)")
            ddb = Dynamodb("DynamoDB\nジョブ状態")

        sns = SimpleNotificationServiceSns("SNS\n完了・失敗通知")
        ecr = ECR("ECR\nコンテナイメージ\n(通常用 / GPU用)")
        logs = CloudwatchLogs("CloudWatch Logs")

    # メインの流れ
    user >> Edge(label="1分動画をアップロード") >> s3_in >> rule >> Edge(label="起動") >> sfn
    sfn >> Edge(label="RunTask\n(タスクトークン待ち)") >> extract
    sfn >> Edge(label="SubmitJob", style="dashed") >> inpaint
    extract >> ocr >> translate >> inpaint >> compose >> qa
    qa >> Edge(label="OK", color="#3F8624") >> publish
    qa >> Edge(label="NG: 指摘を反映して\n再翻訳 (最大2回)", color="#D13212", style="dashed") >> translate

    # 外部・AI サービス
    rules >> Edge(label="代用品ルールを参照", color="#7AA116") >> translate
    translate >> Edge(style="dotted") >> bedrock
    qa >> Edge(label="フレーム画像で確認", style="dotted") >> bedrock

    # 保存
    ocr >> Edge(label="日本語テキスト保存", style="dotted") >> s3_jobs
    publish >> s3_out
    publish >> Edge(style="dotted") >> ddb
    sfn >> Edge(label="結果", style="dotted") >> sns >> Edge(label="メール等") >> user

    gha >> Edge(label="push", color="#232F3E") >> ecr
    ecr >> Edge(label="pull", style="dotted", color="#AAB7B8") >> extract
    ecr >> Edge(label="pull", style="dotted", color="#AAB7B8") >> inpaint
    extract >> Edge(style="dotted", color="#AAB7B8") >> logs
