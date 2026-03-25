FROM python:3.12-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg curl unzip && \
    rm -rf /var/lib/apt/lists/* && \
    pip install --no-cache-dir yt-dlp

# Install Deno (yt-dlp's preferred JS runtime for YouTube challenge solving)
RUN curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh

WORKDIR /app
COPY main.py .
COPY static/ static/

RUN mkdir -p downloads

EXPOSE 8080
CMD ["python3", "main.py"]
