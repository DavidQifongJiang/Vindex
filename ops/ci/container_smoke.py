"""Check a running application container: migrations, built frontend, and database-backed API.

Usage: python ops/ci/container_smoke.py --container NAME --url http://127.0.0.1:8000
/health is static, so it only shows the process started; the other checks prove the rest.
"""
import argparse
import json
import re
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen
from uuid import uuid4

WORKER_MODULES = "app.workers.celery_app, app.workers.pipeline_dispatcher, app.workers.maintenance, app.workers.search_indexing"
SEED = """
import os, sys
from sqlalchemy import create_engine, text
with create_engine(os.environ["DATABASE_URL"]).begin() as db:
    db.execute(text("INSERT INTO videos (video_id, title, status, visibility) VALUES (:id, :id, 'processed', 'public')"), {"id": sys.argv[1]})
"""


def fetch(url):
    with urlopen(url, timeout=10) as response:
        return response.status, response.read()


def in_container(container, *command):
    result = subprocess.run(["docker", "exec", container, *command], capture_output=True, text=True, timeout=180)
    if result.returncode:
        raise AssertionError(f"{' '.join(command[:3])} failed: {result.stdout[-500:]} {result.stderr[-1500:]}")
    return result.stdout


def wait_for_health(container, url, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}", container],
                               capture_output=True, text=True, timeout=30).stdout.strip()
        assert state == "true", f"Container {container} is not running"
        try:
            if fetch(url + "/health")[0] == 200:
                return
        except (URLError, ConnectionError, OSError):
            pass
        time.sleep(2)
    raise AssertionError(f"/health did not respond within {timeout}s")


def check_migrations(container):
    current = in_container(container, "alembic", "current")
    assert "(head)" in current, f"Container database is not at head: {current}"


def check_frontend(url):
    # main.py falls back to a legacy index.html, so a 200 from / alone proves nothing.
    html = fetch(url + "/")[1].decode()
    assets = re.findall(r'/assets/[^"\']+\.(?:js|css)', html)
    assert any(asset.endswith(".js") for asset in assets), "/ is not the built frontend (no hashed script)"
    for asset in assets:
        status, body = fetch(url + asset)
        assert status == 200 and body, f"Could not load {asset}"


def check_database_api(container, url):
    video_id = "ci-smoke-" + uuid4().hex
    assert video_id not in fetch(url + "/public/videos")[1].decode(), "Seed ID already present"
    in_container(container, "python", "-c", SEED, video_id)
    videos = json.loads(fetch(url + "/public/videos")[1])["videos"]
    assert video_id in {video["video_id"] for video in videos}, "API did not return the row seeded in this database"


def check_worker_imports(container):
    in_container(container, "python", "-c", f"import {WORKER_MODULES}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--container", required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--startup-timeout", type=int, default=180)
    args = parser.parse_args()
    wait_for_health(args.container, args.url, args.startup_timeout)
    print("PASS: /health responded")
    check_migrations(args.container)
    print("PASS: container migrated its database to head")
    check_frontend(args.url)
    print("PASS: built frontend and its assets are served")
    check_database_api(args.container, args.url)
    print("PASS: /public/videos returned a row seeded in the disposable database")
    check_worker_imports(args.container)
    print("PASS: worker modules import in the image")
    return 0


if __name__ == "__main__":
    sys.exit(main())
