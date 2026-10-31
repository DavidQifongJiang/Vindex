"""A small video chunk planner for learning CI/CD. No third-party packages."""
import json
import math
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


def chunk_count(seconds, chunk_seconds=300):
    """Count all chunks, including a final partial chunk."""
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("seconds must be a finite positive number")
    if not math.isfinite(chunk_seconds) or chunk_seconds <= 0:
        raise ValueError("chunk_seconds must be a finite positive number")
    return math.ceil(seconds / chunk_seconds)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        request = urlparse(self.path)
        metadata = {
            "service": "cicd-chunk-planner",
            "release": os.getenv("RELEASE_SHA", "local"),
            "environment": os.getenv("LAB_ENV", "development"),
        }
        if request.path == "/health":
            self.respond(200, {"status": "alive", **metadata})
        elif request.path == "/ready":
            failed = os.getenv("DEMO_FAIL_READY", "0") == "1"
            self.respond(503 if failed else 200, {
                "status": "not-ready" if failed else "ready", **metadata,
            })
        elif request.path == "/plan":
            try:
                seconds = float(parse_qs(request.query).get("seconds", [""])[0])
                count = chunk_count(seconds)
            except (ValueError, OverflowError):
                self.respond(400, {"error": "seconds must be a finite positive number"})
            else:
                self.respond(200, {"seconds": seconds, "chunks": count, **metadata})
        else:
            self.respond(404, {"error": "not found"})

    def respond(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format_string, *args):
        print(format_string % args, flush=True)


if __name__ == "__main__":
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "18080"))
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Chunk planner listening on http://{host}:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
