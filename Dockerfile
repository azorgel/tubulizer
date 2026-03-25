FROM python:3.12-slim

# Install ffmpeg and yt-dlp
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/* && \
    pip install --no-cache-dir yt-dlp

WORKDIR /app
COPY main.py .
COPY static/ static/

RUN mkdir -p downloads

EXPOSE 8080
CMD ["python3", "main.py"]
