FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir -U yt-dlp qrcode[pil]
WORKDIR /app
COPY index.html styles.css app.js server.py stream_metadata.py ./
ENV STREAMFORGE_DATA=/data STREAMFORGE_DOWNLOADS=/downloads PORT=8081
VOLUME ["/data", "/downloads", "/plugins"]
EXPOSE 8081
CMD ["python3", "server.py"]
