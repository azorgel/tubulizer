FROM python:3.12-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg curl unzip && \
    rm -rf /var/lib/apt/lists/*

# Install latest yt-dlp binary directly from GitHub (always up to date)
RUN curl -L "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp_linux" \
    -o /usr/local/bin/yt-dlp && chmod a+rx /usr/local/bin/yt-dlp

# Install Deno (yt-dlp's preferred JS runtime)
RUN curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh

WORKDIR /app
ENV PYTHONUNBUFFERED=1
COPY main.py .
COPY static/ static/

RUN mkdir -p downloads

EXPOSE 8080
CMD ["python3", "main.py"]
