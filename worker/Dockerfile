FROM python:3.11-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg fonts-noto-core fonts-noto-cjk libgomp1 libgl1 libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# PaddleOCR の日本語モデルをビルド時に取得しておく (起動を速くするため)
RUN python -c "from paddleocr import PaddleOCR; PaddleOCR(lang='japan', use_angle_cls=False, show_log=False)"

COPY vt ./vt
ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["python", "-m", "vt.main"]
