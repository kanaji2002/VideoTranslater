#!/usr/bin/env python3
import aws_cdk as cdk

from stacks.pipeline_stack import VideoTranslatePipelineStack

app = cdk.App()
VideoTranslatePipelineStack(app, "VideoTranslatePipeline")
app.synth()
