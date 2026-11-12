# 工程④ (文字消し) 専用の GPU イメージ。AWS Batch の GPU インスタンス (g5/g6) で動かす。
# ビルドはリポジトリのルートをコンテキストにする: docker build -f gpu-worker/Dockerfile .
FROM pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg git curl libgl1 libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/*

# ProPainter (S-Lab License 1.0: 非商用。商用利用は著作者の許諾が必要)
ARG PROPAINTER_COMMIT=326782529a63d997cab80ab21439b92ed422b3d3
RUN git clone https://github.com/sczhou/ProPainter.git /opt/ProPainter \
 && cd /opt/ProPainter && git checkout ${PROPAINTER_COMMIT} && rm -rf .git
RUN pip install --no-cache-dir av addict einops future scipy opencv-python-headless matplotlib \
      scikit-image imageio-ffmpeg pyyaml requests timm yapf "numpy<2" boto3 Pillow
# 学習済みの重みをビルド時に入れておく (起動のたびにダウンロードしない)
RUN mkdir -p /opt/ProPainter/weights && cd /opt/ProPainter/weights \
 && for f in ProPainter.pth recurrent_flow_completion.pth raft-things.pth; do \
      curl -fsSL -o $f https://github.com/sczhou/ProPainter/releases/download/v0.1.0/$f; done

WORKDIR /app
COPY worker/vt ./vt
ENV PYTHONUNBUFFERED=1 PROPAINTER_ROOT=/opt/ProPainter INPAINT_PROVIDER=propainter_local STEP=inpaint
ENTRYPOINT ["python", "-m", "vt.main"]
