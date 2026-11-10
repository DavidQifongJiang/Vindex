"""Opt-in isolated benchmark wrapper; never imported by the production app.

Mount this module into the pinned application image. The normal upload handlers
are unchanged. Control endpoints require a secret cookie and refuse production
database/bucket configuration. No dispatcher or processing worker is started.
"""
import hashlib
import hmac
import json
import os
from pathlib import Path
from threading import Event, Thread
import time

from fastapi import HTTPException, Request
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text


def validate_environment(env):
    from sqlalchemy.engine import make_url
    url = make_url(env.get("DATABASE_URL", "sqlite://"))
    if (env.get("VINDEX_BENCHMARK_ISOLATED") != "true"
            or url.database != "vindex_upload_bench" or url.host != "bench-postgres"
            or not env.get("S3_BUCKET_NAME", "").startswith("vindex-upload-bench-")
            or len(env.get("VINDEX_BENCH_TOKEN", "")) < 32
            or env.get("AUTH_REQUIRED") != "false"):
        raise RuntimeError("Refusing non-isolated benchmark configuration")


def process_snapshot():
    import resource
    usage = resource.getrusage(resource.RUSAGE_SELF)
    io = {k: int(v) for k, v in (line.split(":") for line in Path("/proc/self/io").read_text().splitlines())}
    rss = next(int(line.split()[1]) * 1024 for line in Path("/proc/self/status").read_text().splitlines() if line.startswith("VmRSS:"))
    net = [line.split(":", 1)[1].split() for line in Path("/proc/net/dev").read_text().splitlines()[2:] if not line.strip().startswith("lo:")]
    cpu = dict(line.split() for line in Path("/sys/fs/cgroup/cpu.stat").read_text().splitlines())
    proxy_cpu = 0
    if Path("/proxy-cgroup/cpu.stat").exists():
        proxy_cpu = int(dict(line.split() for line in Path("/proxy-cgroup/cpu.stat").read_text().splitlines())["usage_usec"]) / 1e6
    return {"cpu_seconds": usage.ru_utime + usage.ru_stime, "proxy_cpu_seconds": proxy_cpu, "read_bytes": io["read_bytes"],
            "write_bytes": io["write_bytes"], "cancelled_write_bytes": io["cancelled_write_bytes"],
            "write_syscall_bytes": io["wchar"],
            "network_rx_bytes": sum(int(n[0]) for n in net), "network_tx_bytes": sum(int(n[8]) for n in net),
            "throttled_seconds": int(cpu.get("throttled_usec", 0)) / 1e6,
            "container_memory_bytes": int(Path("/sys/fs/cgroup/memory.current").read_text()),
            "rss_bytes": rss, "wall": time.monotonic()}


class WaveMeter:
    def __init__(self):
        self.active = None
        self.stop = Event()

    def begin(self, name):
        if self.active is not None:
            raise HTTPException(409, "A measured wave is already active")
        self.active = name
        self.bytes_in = self.bytes_out = self.requests = 0
        self.start = process_snapshot()
        self.peak_rss = self.start["rss_bytes"]
        self.peak_memory = self.start["container_memory_bytes"]
        self.stop.clear()
        def sample():
            while not self.stop.wait(0.05):
                snapshot = process_snapshot()
                self.peak_rss = max(self.peak_rss, snapshot["rss_bytes"])
                self.peak_memory = max(self.peak_memory, snapshot["container_memory_bytes"])
        self.thread = Thread(target=sample, daemon=True)
        self.thread.start()
        return {"wave": name, "started": True}

    def end(self, name):
        if self.active != name:
            raise HTTPException(409, "Wave identity mismatch")
        end = process_snapshot()
        self.stop.set()
        self.thread.join(timeout=1)
        self.active = None
        return {"wave": name, "elapsed_seconds": end["wall"] - self.start["wall"],
                **{k: end[k] - self.start[k] for k in ("cpu_seconds", "proxy_cpu_seconds", "read_bytes", "write_bytes", "cancelled_write_bytes", "write_syscall_bytes", "network_rx_bytes", "network_tx_bytes", "throttled_seconds")},
                "rss_start_bytes": self.start["rss_bytes"], "rss_end_bytes": end["rss_bytes"],
                "rss_peak_bytes": max(self.peak_rss, end["rss_bytes"]),
                "container_memory_start_bytes": self.start["container_memory_bytes"],
                "container_memory_peak_bytes": max(self.peak_memory, end["container_memory_bytes"]),
                "api_request_body_bytes": self.bytes_in, "api_response_body_bytes": self.bytes_out,
                "api_requests": self.requests}


def build_app():
    validate_environment(os.environ)
    from app.main import app
    from app.db.session import create_session
    from app.db.models import Video, PipelineJob, UploadRequest
    from app.api.videos import storage

    meter = WaveMeter()
    app.mount("/bench-api", StaticFiles(directory="/bench/frontend-api", html=True))
    app.mount("/bench-direct", StaticFiles(directory="/bench/frontend-direct", html=True))

    @app.get("/bench/control/info")
    def info():
        return {"isolated": True, "database": "vindex_upload_bench", "bucket": storage.bucket,
                "code_commit": os.environ["VINDEX_BENCH_COMMIT"], "api_cpu_limit": 0.5,
                "api_memory_limit_bytes": 2147483648, "processing_workers": 0,
                "https_termination": "separate_proxy" if Path("/proxy-cgroup/cpu.stat").exists() else "api_process",
                "measurement": "ASGI bodies; process CPU and /proc I/O; container network and memory; RSS/memory sampled at 50ms"}

    @app.post("/bench/control/start")
    async def start(request: Request):
        return meter.begin((await request.json())["wave"])

    @app.post("/bench/control/end")
    async def end(request: Request):
        return meter.end((await request.json())["wave"])

    @app.post("/bench/control/verify-cleanup")
    def cleanup(payload: dict):
        if meter.active is not None:
            raise HTTPException(409, "End measurement before verification")
        expected = payload["videos"]
        if len(expected) != len(set(expected)) or len(expected) > 10:
            raise HTTPException(400, "Expected unique IDs from a bounded wave")
        db = create_session()
        try:
            videos = db.query(Video).all()
            if {v.video_id for v in videos} != set(expected):
                raise HTTPException(409, "Database does not match accepted wave; preserve evidence")
            verified = []
            for video in videos:
                jobs = db.query(PipelineJob).filter_by(video_id=video.video_id).all()
                reservations = db.query(UploadRequest).filter_by(video_id=video.video_id).all()
                if sorted(j.stage for j in jobs) != ["playback", "search"] or any(j.status != "pending" for j in jobs):
                    raise HTTPException(409, "Initial durable jobs differ from expected isolated state")
                if len(reservations) != 1 or reservations[0].status != "completed":
                    raise HTTPException(409, "Expected one completed upload reservation")
                body = storage.client.get_object(Bucket=storage.bucket, Key=video.raw_path)["Body"]
                digest, size = hashlib.sha256(), 0
                try:
                    for chunk in body.iter_chunks(chunk_size=1048576):
                        digest.update(chunk)
                        size += len(chunk)
                finally:
                    body.close()
                if digest.hexdigest() != payload["sha256"] or size != payload["bytes"]:
                    raise HTTPException(409, "Accepted input integrity mismatch")
                verified.append({"video_id": video.video_id, "bytes": size, "sha256_verified": True, "initial_jobs": 2})
            # Only run-owned video prefixes in this dedicated bucket are eligible.
            for video in videos:
                storage.delete_prefix(f"raw_videos/{video.video_id}/")
                storage.delete_many([video.raw_path, f"metrics/{video.video_id}.json"])
            for model in (PipelineJob, UploadRequest, Video):
                db.query(model).filter(model.video_id.in_(expected)).delete(synchronize_session=False)
            db.commit()
            remaining = sum(storage.client.list_objects_v2(Bucket=storage.bucket, Prefix=f"raw_videos/{v}/").get("KeyCount", 0) for v in expected)
            if remaining:
                raise HTTPException(409, "Test object cleanup incomplete")
            return {"verified": verified, "remaining_test_objects": remaining,
                    "remaining_videos": db.query(Video).count(), "remaining_jobs": db.query(PipelineJob).count()}
        finally:
            db.close()

    class GuardAndMeter:
        async def __call__(self, scope, receive, send):
            if scope["type"] != "http":
                return await app(scope, receive, send)
            from http.cookies import SimpleCookie
            cookies = SimpleCookie()
            cookies.load(dict(scope["headers"]).get(b"cookie", b"").decode("latin-1"))
            supplied = cookies.get("vindex_bench")
            if not supplied or not hmac.compare_digest(supplied.value, os.environ["VINDEX_BENCH_TOKEN"]):
                await send({"type": "http.response.start", "status": 403, "headers": []})
                return await send({"type": "http.response.body", "body": b"Benchmark access required"})
            measured = meter.active is not None and not scope["path"].startswith("/bench")
            if measured:
                meter.requests += 1
            async def counted_receive():
                message = await receive()
                if measured and message["type"] == "http.request":
                    meter.bytes_in += len(message.get("body", b""))
                return message
            async def counted_send(message):
                if measured and message["type"] == "http.response.body":
                    meter.bytes_out += len(message.get("body", b""))
                await send(message)
            await app(scope, counted_receive, counted_send)
    return GuardAndMeter()


if os.getenv("VINDEX_BENCHMARK_ISOLATED") == "true":
    application = build_app()
