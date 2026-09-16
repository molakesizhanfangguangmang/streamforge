FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir -U yt-dlp qrcode[pil]
WORKDIR /app
# 构建戳：CI 构建时用 --build-arg 传进来，本地 docker build 保持 unknown，
# 由 /api/health 报出去，界面才能区分「CI 出的镜像」和「本地构建/热部署」。
ARG BUILD_SHA=unknown
ARG BUILD_TIME=unknown
COPY index.html styles.css app.js server.py stream_metadata.py favicon.svg ./
ENV STREAMFORGE_DATA=/data STREAMFORGE_DOWNLOADS=/downloads PORT=8081 \
    STREAMFORGE_VERSION=1.0.0 STREAMFORGE_BUILD_SHA=$BUILD_SHA STREAMFORGE_BUILD_TIME=$BUILD_TIME
VOLUME ["/data", "/downloads", "/plugins"]
EXPOSE 8081
CMD ["python3", "server.py"]
