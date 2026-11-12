"""動画テロップ翻訳パイプライン (S3 -> Step Functions -> ECS Fargate ワーカー)."""

from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Size,
    Stack,
    aws_batch as batch,
    aws_dynamodb as ddb,
    aws_ec2 as ec2,
    aws_ecr_assets as ecr_assets,
    aws_ecs as ecs,
    aws_events as events,
    aws_events_targets as targets,
    aws_iam as iam,
    aws_logs as logs,
    aws_s3 as s3,
    aws_secretsmanager as secretsmanager,
    aws_sns as sns,
    aws_stepfunctions as sfn,
    aws_stepfunctions_tasks as tasks,
)
from constructs import Construct

WORKER_STEPS = ["extract", "ocr", "translate", "inpaint", "compose", "qa", "publish"]


class VideoTranslatePipelineStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        bedrock_region = self.node.try_get_context("bedrockRegion") or "us-east-1"
        bedrock_model_id = self.node.try_get_context("bedrockModelId") or "anthropic.claude-opus-5-5"
        inpaint_provider = self.node.try_get_context("inpaintProvider") or "propainter_local"
        # Claude の呼び出し経路: "bedrock" か "anthropic" (Anthropic API を直接)
        llm_provider = self.node.try_get_context("llmProvider") or "bedrock"
        anthropic_model_id = self.node.try_get_context("anthropicModelId") or "claude-opus-5-5"
        max_qa_attempts = int(self.node.try_get_context("maxQaAttempts") or 2)

        # ---------- Storage ----------
        bucket = s3.Bucket(
            self,
            "MediaBucket",
            event_bridge_enabled=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            lifecycle_rules=[
                # 中間ファイル (フレーム画像など) は 30 日で削除
                s3.LifecycleRule(prefix="jobs/", expiration=Duration.days(30)),
            ],
            removal_policy=RemovalPolicy.RETAIN,
        )

        jobs_table = ddb.Table(
            self,
            "JobsTable",
            partition_key=ddb.Attribute(name="job_id", type=ddb.AttributeType.STRING),
            billing_mode=ddb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN,
        )

        inpaint_secret = secretsmanager.Secret(
            self,
            "InpaintApiSecret",
            description="文字除去/インペイント API の認証情報 (JSON: {\"api_key\": \"...\", \"endpoint\": \"...\"})",
        )

        # llmProvider=anthropic のときに使う Anthropic API キー。デプロイ後にコンソールで値を入れる
        anthropic_secret = secretsmanager.Secret(
            self,
            "AnthropicApiSecret",
            description="Anthropic API キー (JSON: {\"api_key\": \"sk-ant-...\"})",
        )

        notify_topic = sns.Topic(self, "NotifyTopic", display_name="VideoTranslate notifications")

        # ---------- Compute ----------
        vpc = ec2.Vpc(
            self,
            "Vpc",
            max_azs=2,
            nat_gateways=0,
            subnet_configuration=[
                ec2.SubnetConfiguration(name="public", subnet_type=ec2.SubnetType.PUBLIC)
            ],
        )
        vpc.add_gateway_endpoint("S3Endpoint", service=ec2.GatewayVpcEndpointAwsService.S3)

        cluster = ecs.Cluster(self, "Cluster", vpc=vpc, container_insights_v2=ecs.ContainerInsights.ENABLED)

        task_def = ecs.FargateTaskDefinition(
            self,
            "WorkerTask",
            cpu=4096,
            memory_limit_mib=16384,
            ephemeral_storage_gib=50,
            runtime_platform=ecs.RuntimePlatform(
                cpu_architecture=ecs.CpuArchitecture.X86_64,
                operating_system_family=ecs.OperatingSystemFamily.LINUX,
            ),
        )
        container = task_def.add_container(
            "worker",
            image=ecs.ContainerImage.from_asset("../worker", platform=ecr_assets.Platform.LINUX_AMD64),
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix="worker", log_retention=logs.RetentionDays.ONE_MONTH
            ),
            environment={
                "BUCKET": bucket.bucket_name,
                "JOBS_TABLE": jobs_table.table_name,
                "BEDROCK_REGION": bedrock_region,
                "BEDROCK_MODEL_ID": bedrock_model_id,
                "LLM_PROVIDER": llm_provider,
                "ANTHROPIC_MODEL_ID": anthropic_model_id,
                "ANTHROPIC_SECRET_ARN": anthropic_secret.secret_arn,
                "INPAINT_PROVIDER": inpaint_provider,
                "INPAINT_SECRET_ARN": inpaint_secret.secret_arn,
                # Bedrock プロンプト管理のプロンプト ("ID" か "ID:バージョン")。空ならコード内のプロンプト
                "TRANSLATE_PROMPT_ID": self.node.try_get_context("translatePromptId") or "",
                "QA_PROMPT_ID": self.node.try_get_context("qaPromptId") or "",
            },
        )

        bucket.grant_read_write(task_def.task_role)
        jobs_table.grant_read_write_data(task_def.task_role)
        inpaint_secret.grant_read(task_def.task_role)
        anthropic_secret.grant_read(task_def.task_role)
        task_def.task_role.add_to_principal_policy(
            iam.PolicyStatement(
                # Bedrock (Mantle エンドポイント経由の Messages API)。
                # 本番ではモデル ARN / 推論プロファイル ARN に絞ってください。
                actions=[
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                    "bedrock-mantle:*",
                    "bedrock:GetPrompt",
                ],
                resources=["*"],
            )
        )
        task_def.task_role.add_to_principal_policy(
            iam.PolicyStatement(
                actions=["states:SendTaskSuccess", "states:SendTaskFailure", "states:SendTaskHeartbeat"],
                resources=["*"],
            )
        )

        # ---------- State machine ----------
        def worker_step(
            step: str,
            extra_env: dict[str, str] | None = None,
            timeout_min: int = 30,
            state_id: str | None = None,
        ):
            env = [
                tasks.TaskEnvironmentVariable(name="STEP", value=step),
                tasks.TaskEnvironmentVariable(name="TASK_TOKEN", value=sfn.JsonPath.task_token),
                tasks.TaskEnvironmentVariable(name="JOB_ID", value=sfn.JsonPath.string_at("$.job_id")),
                tasks.TaskEnvironmentVariable(name="INPUT_KEY", value=sfn.JsonPath.string_at("$.input_key")),
                tasks.TaskEnvironmentVariable(
                    name="ATTEMPT", value=sfn.JsonPath.json_to_string(sfn.JsonPath.number_at("$.attempt"))
                ),
            ]
            for k, v in (extra_env or {}).items():
                env.append(tasks.TaskEnvironmentVariable(name=k, value=v))
            return tasks.EcsRunTask(
                self,
                state_id or step.capitalize(),
                integration_pattern=sfn.IntegrationPattern.WAIT_FOR_TASK_TOKEN,
                cluster=cluster,
                task_definition=task_def,
                launch_target=tasks.EcsFargateLaunchTarget(
                    platform_version=ecs.FargatePlatformVersion.LATEST
                ),
                assign_public_ip=True,
                subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
                container_overrides=[
                    tasks.ContainerOverride(container_definition=container, environment=env)
                ],
                result_path=f"$.results.{step}",
                task_timeout=sfn.Timeout.duration(Duration.minutes(timeout_min)),
            ).add_retry(errors=["ECS.AmazonECSException", "States.Timeout"], max_attempts=2)

        init = sfn.Pass(
            self,
            "Init",
            parameters={
                "job_id": sfn.JsonPath.string_at("$$.Execution.Name"),
                "input_key": sfn.JsonPath.string_at("$.detail.object.key"),
                "attempt": 0,
                "results": {},
            },
        )

        mark_failed = tasks.DynamoUpdateItem(
            self,
            "MarkFailed",
            table=jobs_table,
            key={"job_id": tasks.DynamoAttributeValue.from_string(sfn.JsonPath.string_at("$.job_id"))},
            update_expression="SET #s = :s, #e = :e",
            expression_attribute_names={"#s": "status", "#e": "error"},
            expression_attribute_values={
                ":s": tasks.DynamoAttributeValue.from_string("failed"),
                ":e": tasks.DynamoAttributeValue.from_string(sfn.JsonPath.json_to_string(sfn.JsonPath.object_at("$.error"))),
            },
            result_path=sfn.JsonPath.DISCARD,
        ).next(
            tasks.SnsPublish(
                self,
                "NotifyFailed",
                topic=notify_topic,
                message=sfn.TaskInput.from_json_path_at("$"),
                subject="VideoTranslate: job failed",
            )
        ).next(sfn.Fail(self, "Failed"))

        extract = worker_step("extract")
        ocr = worker_step("ocr", timeout_min=60)
        translate = worker_step("translate")
        if inpaint_provider == "propainter_local":
            inpaint = self._gpu_inpaint_step(vpc, bucket, jobs_table)
        else:
            inpaint = worker_step("inpaint", timeout_min=90)
        compose = worker_step("compose")
        qa = worker_step("qa")
        publish_ok = worker_step("publish", {"PUBLISH_STATUS": "approved"}, state_id="PublishApproved")
        publish_review = worker_step("publish", {"PUBLISH_STATUS": "needs_review"}, state_id="PublishNeedsReview")

        for t in [extract, ocr, translate, inpaint, compose, qa, publish_ok, publish_review]:
            t.add_catch(mark_failed, result_path="$.error")

        notify_done = tasks.SnsPublish(
            self,
            "NotifyDone",
            topic=notify_topic,
            message=sfn.TaskInput.from_json_path_at("$.results.publish"),
            subject="VideoTranslate: job finished",
        )

        retry_translate = sfn.Pass(
            self,
            "IncrementAttempt",
            parameters={
                "job_id": sfn.JsonPath.string_at("$.job_id"),
                "input_key": sfn.JsonPath.string_at("$.input_key"),
                "attempt": sfn.JsonPath.math_add(sfn.JsonPath.number_at("$.attempt"), 1),
                "results": sfn.JsonPath.object_at("$.results"),
            },
        )

        qa_choice = (
            sfn.Choice(self, "QaPassed?")
            .when(sfn.Condition.boolean_equals("$.results.qa.passed", True), publish_ok)
            .when(
                sfn.Condition.number_less_than("$.attempt", max_qa_attempts),
                retry_translate,
            )
            .otherwise(publish_review)
        )

        # 再試行は「QA の指摘を反映して再翻訳 -> 再合成 -> 再QA」
        retry_translate.next(translate)
        publish_ok.next(notify_done)
        publish_review.next(notify_done)

        definition = (
            init.next(extract)
            .next(ocr)
            .next(translate)
            .next(inpaint)
            .next(compose)
            .next(qa)
            .next(qa_choice)
        )
        # translate -> inpaint は初回のみ必要だが、inpaint は結果をキャッシュするので再実行でも即終了する

        state_machine = sfn.StateMachine(
            self,
            "Pipeline",
            definition_body=sfn.DefinitionBody.from_chainable(definition),
            timeout=Duration.hours(3),
            tracing_enabled=True,
            logs=sfn.LogOptions(
                destination=logs.LogGroup(self, "PipelineLogs", retention=logs.RetentionDays.ONE_MONTH),
                level=sfn.LogLevel.ERROR,
            ),
        )

        # ---------- Trigger: s3://bucket/input/*.mp4 ----------
        events.Rule(
            self,
            "OnVideoUploaded",
            event_pattern=events.EventPattern(
                source=["aws.s3"],
                detail_type=["Object Created"],
                detail={
                    "bucket": {"name": [bucket.bucket_name]},
                    "object": {"key": [{"wildcard": "input/*.mp4"}]},
                },
            ),
            targets=[targets.SfnStateMachine(state_machine)],
        )

        CfnOutput(self, "BucketName", value=bucket.bucket_name)
        CfnOutput(self, "StateMachineArn", value=state_machine.state_machine_arn)
        CfnOutput(self, "JobsTableName", value=jobs_table.table_name)
        CfnOutput(self, "InpaintSecretArn", value=inpaint_secret.secret_arn)
        CfnOutput(self, "AnthropicSecretArn", value=anthropic_secret.secret_arn)
        CfnOutput(self, "NotifyTopicArn", value=notify_topic.topic_arn)

    def _gpu_inpaint_step(self, vpc: ec2.IVpc, bucket: s3.IBucket, jobs_table: ddb.ITable) -> sfn.IChainable:
        """工程④ を AWS Batch の GPU インスタンスで ProPainter 実行する。

        ジョブが無いときはインスタンス 0 台 (課金なし)。ジョブが来たら起動し、終われば自動で止まる。
        """
        instance_types = self.node.try_get_context("gpuInstanceTypes") or ["g6.xlarge", "g5.xlarge"]
        compute_env = batch.ManagedEc2EcsComputeEnvironment(
            self,
            "GpuComputeEnv",
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            instance_types=[ec2.InstanceType(t) for t in instance_types],
            use_optimal_instance_classes=False,
            images=[batch.EcsMachineImage(image_type=batch.EcsMachineImageType.ECS_AL2_NVIDIA)],
            minv_cpus=0,
            maxv_cpus=int(self.node.try_get_context("gpuMaxVcpus") or 8),
            spot=bool(self.node.try_get_context("gpuSpot")),
        )
        queue = batch.JobQueue(self, "GpuQueue", compute_environments=[
            batch.OrderedComputeEnvironment(compute_environment=compute_env, order=1)
        ])

        job_role = iam.Role(self, "GpuJobRole", assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"))
        bucket.grant_read_write(job_role)
        jobs_table.grant_read_write_data(job_role)

        job_def = batch.EcsJobDefinition(
            self,
            "GpuInpaintJob",
            timeout=Duration.minutes(90),
            container=batch.EcsEc2ContainerDefinition(
                self,
                "GpuInpaintContainer",
                image=ecs.ContainerImage.from_asset(
                    "..",
                    file="gpu-worker/Dockerfile",
                    platform=ecr_assets.Platform.LINUX_AMD64,  # M1/M2 Mac でも x86 用にビルド
                    exclude=["**/cdk.out", "**/.git", "docs", "infra", "**/__pycache__", "**/.venv"],
                ),
                cpu=4,
                gpu=1,
                memory=Size.mebibytes(14000),
                job_role=job_role,
                logging=ecs.LogDrivers.aws_logs(
                    stream_prefix="gpu-inpaint", log_retention=logs.RetentionDays.ONE_MONTH
                ),
                environment={
                    "BUCKET": bucket.bucket_name,
                    "JOBS_TABLE": jobs_table.table_name,
                    "INPAINT_PROVIDER": "propainter_local",
                    "PROPAINTER_SUBVIDEO_LENGTH": str(self.node.try_get_context("propainterSubvideoLength") or 50),
                },
            ),
        )

        return tasks.BatchSubmitJob(
            self,
            "Inpaint",
            job_name="inpaint",
            job_queue_arn=queue.job_queue_arn,
            job_definition_arn=job_def.job_definition_arn,
            container_overrides=tasks.BatchContainerOverrides(
                environment={
                    "STEP": "inpaint",
                    "JOB_ID": sfn.JsonPath.string_at("$.job_id"),
                    "INPUT_KEY": sfn.JsonPath.string_at("$.input_key"),
                    "ATTEMPT": sfn.JsonPath.json_to_string(sfn.JsonPath.number_at("$.attempt")),
                }
            ),
            result_selector={"batch_job_id": sfn.JsonPath.string_at("$.JobId"), "status": sfn.JsonPath.string_at("$.Status")},
            result_path="$.results.inpaint",
        ).add_retry(errors=["Batch.AWSBatchException"], max_attempts=2)
