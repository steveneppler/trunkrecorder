# CUDA 12 + cuDNN 9 runtime. faster-whisper's engine (CTranslate2 4.x) is built
# against these; using a plain python base image will fail at model load with a
# missing libcudnn error.
FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # Keep model weights on the mounted volume so they survive a rebuild.
    HF_HOME=/models \
    XDG_CACHE_HOME=/models

RUN apt-get update && apt-get install --no-install-recommends -y \
      python3 \
      python3-pip \
      ffmpeg \
      ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

COPY *.py ./

CMD ["python3", "-u", "transcriber.py"]
