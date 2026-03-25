#!/usr/bin/env python3
"""
Tubulizer — YouTube video extractor
Pure stdlib backend (no pip required). Uses yt-dlp CLI via subprocess.
"""

import json
import os
import shutil
import subprocess
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from socketserver import ThreadingMixIn
from urllib.parse import quote, unquote, urlparse

# ── Config ──────────────────────────────────────────────────

PORT         = int(os.environ.get("PORT", 8080))
STATIC_DIR   = Path(__file__).parent / "static"
DOWNLOAD_DIR = Path(__file__).parent / "downloads"

# Prefer a bundled yt-dlp wrapper (set by build_app.sh) over Homebrew
_bundled_ytdlp = Path(__file__).parent.parent / "bin" / "yt-dlp"
YT_DLP = str(_bundled_ytdlp) if _bundled_ytdlp.exists() else \
         shutil.which("yt-dlp") or "/opt/homebrew/bin/yt-dlp"

CHUNK        = 1024 * 256   # 256 KB streaming chunks

DOWNLOAD_DIR.mkdir(exist_ok=True)

MIME = {
    ".html": "text/html; charset=utf-8",
    ".css":  "text/css; charset=utf-8",
    ".js":   "application/javascript; charset=utf-8",
    ".json": "application/json",
    ".ico":  "image/x-icon",
    ".mp4":  "video/mp4",
    ".webm": "video/webm",
    ".mkv":  "video/x-matroska",
    ".m4a":  "audio/mp4",
    ".mp3":  "audio/mpeg",
    ".opus": "audio/ogg",
}

COOKIES_FILE     = Path(__file__).parent / "cookies.txt"
COOKIES_PASSWORD = os.environ.get("COOKIES_PASSWORD", "")


def get_extra_args():
    args = []
    if COOKIES_FILE.exists():
        args += ["--cookies", str(COOKIES_FILE)]
    args += [
        "--extractor-args", "youtube:player_client=web",
        "--js-runtimes", "node",
        "--remote-components", "ejs:github",
    ]
    return args

# Quality presets shown to the user.
# Each maps to a yt-dlp format selector.
PRESETS = [
    {"id": "best",  "label": "Best available",  "kind": "video", "height": None, "fmt": "bestvideo[height>=720]+bestaudio/bestvideo+bestaudio/best"},
    {"id": "1080",  "label": "1080p HD",         "kind": "video", "height": 1080, "fmt": "bestvideo[height<=1080][height>=720]+bestaudio/bestvideo[height<=1080]+bestaudio/best[height<=1080]/bestvideo+bestaudio"},
    {"id": "720",   "label": "720p HD",          "kind": "video", "height": 720,  "fmt": "bestvideo[height<=720][height>=480]+bestaudio/bestvideo[height<=720]+bestaudio/best[height<=720]/bestvideo+bestaudio"},
    {"id": "480",   "label": "480p",             "kind": "video", "height": 480,  "fmt": "bestvideo[height<=480]+bestaudio/best[height<=480]/bestvideo+bestaudio"},
    {"id": "360",   "label": "360p",             "kind": "video", "height": 360,  "fmt": "bestvideo[height<=360]+bestaudio/best[height<=360]/bestvideo+bestaudio"},
    {"id": "audio", "label": "Audio only (m4a)", "kind": "audio", "height": None, "fmt": "bestaudio[ext=m4a]/bestaudio/best"},
]
PRESET_BY_ID = {p["id"]: p for p in PRESETS}


# ── Helpers ──────────────────────────────────────────────────

def _cleanup_old_downloads(max_age_seconds=3600):
    """Delete download folders older than max_age_seconds (default 1 hour)."""
    cutoff = time.time() - max_age_seconds
    for folder in DOWNLOAD_DIR.iterdir():
        if folder.is_dir() and folder.stat().st_mtime < cutoff:
            shutil.rmtree(folder, ignore_errors=True)

def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=600)


def yt_error(r):
    return r.stderr.strip() or r.stdout.strip() or "yt-dlp failed"


def get_info(url):
    r = run([YT_DLP, "--dump-json", "--no-playlist", *get_extra_args(), url])
    if r.returncode != 0:
        raise RuntimeError(yt_error(r))
    return json.loads(r.stdout)


def available_presets(info):
    """Return the subset of PRESETS that make sense for this video."""
    heights = set()
    has_audio = False
    for f in info.get("formats", []):
        h = f.get("height")
        if h:
            heights.add(h)
        if (f.get("acodec") or "none") != "none":
            has_audio = True

    max_h = max(heights) if heights else 0

    result = []
    for p in PRESETS:
        if p["kind"] == "audio":
            if has_audio:
                result.append(p)
        else:
            ph = p["height"]
            if ph is None:           # "Best available" always shown
                result.append(p)
            elif max_h >= ph:        # Only show if video goes that high
                result.append(p)
    return result


# ── Threaded server ──────────────────────────────────────────

class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


# ── HTTP Handler ─────────────────────────────────────────────

class TubulizerHandler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        print(f"  {self.address_string()} {fmt % args}")

    def send_json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, code, detail):
        self.send_json(code, {"detail": detail})

    # ── GET ────────────────────────────────────────────────

    def do_GET(self):
        parsed = urlparse(self.path)
        path   = unquote(parsed.path.rstrip("/") or "/")

        if path in ("/", "/index.html"):
            self.serve_file(STATIC_DIR / "index.html")
        elif path.startswith("/static/"):
            self.serve_file(STATIC_DIR / path[len("/static/"):])
        elif path.startswith("/downloads/"):
            self.serve_file(DOWNLOAD_DIR / path[len("/downloads/"):], attachment=True)
        elif path == "/api/cookies-status":
            self.send_json(200, {"active": COOKIES_FILE.exists()})
        else:
            self.send_response(404)
            self.end_headers()

    def serve_file(self, file_path, attachment=False):
        file_path = Path(file_path)
        if not file_path.exists() or not file_path.is_file():
            self.send_response(404)
            self.end_headers()
            return
        try:
            file_path.resolve().relative_to(Path(__file__).parent.resolve())
        except ValueError:
            self.send_response(403)
            self.end_headers()
            return

        mime      = MIME.get(file_path.suffix.lower(), "application/octet-stream")
        file_size = file_path.stat().st_size
        safe_name = quote(file_path.name)

        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(file_size))
        if attachment:
            self.send_header(
                "Content-Disposition",
                f"attachment; filename*=UTF-8''{safe_name}"
            )
        self.end_headers()

        # Stream in chunks — never load the whole file into RAM
        try:
            with open(file_path, "rb") as fh:
                while True:
                    chunk = fh.read(CHUNK)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass  # Client cancelled the download — not an error

    # ── OPTIONS ───────────────────────────────────────────

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    # ── POST ──────────────────────────────────────────────

    def do_POST(self):
        path   = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", 0))
        body   = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self.send_error_json(400, "Invalid JSON")
            return

        if path == "/api/info":
            self.handle_info(data)
        elif path == "/api/download":
            self.handle_download(data)
        elif path == "/api/cookies":
            self.handle_cookies(data)
        else:
            self.send_response(404)
            self.end_headers()

    def handle_info(self, data):
        url = (data.get("url") or "").strip()
        if not url:
            self.send_error_json(400, "Missing URL")
            return
        try:
            info    = get_info(url)
            presets = available_presets(info)
            self.send_json(200, {
                "title":      info.get("title", "Unknown"),
                "duration":   info.get("duration"),
                "thumbnail":  info.get("thumbnail"),
                "uploader":   info.get("uploader", "Unknown"),
                "view_count": info.get("view_count"),
                "formats":    presets,
            })
        except RuntimeError as e:
            self.send_error_json(400, str(e))
        except Exception as e:
            self.send_error_json(500, str(e))

    def handle_download(self, data):
        url       = (data.get("url") or "").strip()
        preset_id = (data.get("format_id") or "best").strip()
        if not url:
            self.send_error_json(400, "Missing url")
            return

        preset = PRESET_BY_ID.get(preset_id, PRESET_BY_ID["best"])
        fmt    = preset["fmt"]

        job_id  = uuid.uuid4().hex[:8]
        out_dir = DOWNLOAD_DIR / job_id
        out_dir.mkdir(exist_ok=True)

        extra = ["--audio-format", "m4a"] if preset["kind"] == "audio" else \
                ["--merge-output-format", "mp4"]

        cmd = [
            YT_DLP,
            "--no-playlist",
            "-f", fmt,
            *extra,
            *get_extra_args(),
            "--retries", "3",
            "--fragment-retries", "3",
            "-o", str(out_dir / "%(title)s.%(ext)s"),
            url,
        ]
        try:
            r = run(cmd)
            if r.returncode != 0:
                raise RuntimeError(yt_error(r))

            files = list(out_dir.iterdir())
            if not files:
                raise RuntimeError("Download produced no file")

            file_path = files[0]
            self.send_json(200, {
                "path":     f"/downloads/{job_id}/{quote(file_path.name)}",
                "filename": file_path.name,
            })
            _cleanup_old_downloads()
        except RuntimeError as e:
            self.send_error_json(400, str(e))
        except Exception as e:
            self.send_error_json(500, str(e))


    def handle_cookies(self, data):
        if COOKIES_PASSWORD:
            if (data.get("password") or "").strip() != COOKIES_PASSWORD:
                self.send_error_json(403, "Invalid password")
                return
        content = (data.get("content") or "").strip()
        if not content:
            self.send_error_json(400, "No cookie content provided")
            return
        COOKIES_FILE.write_text(content)
        self.send_json(200, {"ok": True})


# ── Main ─────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"""
  ╔══════════════════════════════════╗
  ║         T U B U L I Z E R       ║
  ║      YouTube Video Extractor     ║
  ╚══════════════════════════════════╝

  Open:  http://localhost:{PORT}
  yt-dlp: {YT_DLP}
  Press  Ctrl+C  to stop.
""")

    server = ThreadedHTTPServer(("", PORT), TubulizerHandler)
    server.serve_forever()
