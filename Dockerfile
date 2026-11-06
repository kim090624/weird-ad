# 서버 쪽에 ffmpeg + tesseract(한국어 데이터)를 직접 설치하므로 접속자는 아무것도 설치할 필요가 없다.
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    tesseract-ocr \
    tesseract-ocr-kor \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# 음성 인식 모델 크기. Render 무료(메모리 512MB)는 base가 안전하다. 더 정확하게: small
ENV WHISPER_MODEL=base
# 음성 인식 모델을 이미지에 미리 받아 둔다 (첫 요청이 느려지지 않게)
RUN python -c "from faster_whisper import WhisperModel; WhisperModel('base', device='cpu', compute_type='int8')"

# Render가 PORT를 넘겨준다. 영상 하나에 수십 초가 걸려서 timeout을 넉넉히 둔다.
CMD gunicorn -w 1 --threads 4 --timeout 300 -b 0.0.0.0:${PORT:-5000} app:app
