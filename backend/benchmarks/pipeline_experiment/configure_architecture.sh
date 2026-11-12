#!/bin/bash
# Run only on an already seeded, dedicated benchmark host after uploading sources.
set -euo pipefail
cd /opt/vindex-experiment
test -f results/seed-verification.json
if systemctl is-active --quiet mixed-campaign.service; then exit 3; fi
docker exec bench-api python -c 'import os; from guard import validate_environment; validate_environment(os.environ)'
source runtime.env
docker stop -t 30 bench-api bench-search bench-dispatcher bench-playback
docker rm bench-api bench-search bench-dispatcher
printf '%s\n' '{"architecture":"dag"}' > architecture.json
docker run -d --name bench-api --network pipeline-experiment --cpus=.5 --memory=2g --memory-swap=2g --env-file app.env -v /opt/vindex-experiment:/bench:ro "$APP_IMAGE" uvicorn architecture_server:application --host 0.0.0.0 --port 8000 --no-access-log
docker run -d --name bench-dispatcher --network pipeline-experiment --cpus=.25 --memory=512m --memory-swap=512m --env-file app.env -v /opt/vindex-experiment:/bench:ro "$APP_IMAGE" python /bench/architecture_control.py
docker run -d --name bench-search --network pipeline-experiment --cpus=4 --memory=10g --memory-swap=10g --env-file app.env -v /opt/vindex-experiment:/bench:ro "$APP_IMAGE" celery -A architecture_worker.celery_app worker --loglevel=info --pool=prefork --concurrency=4 -Q search,playback,celery -n search@experiment
docker exec bench-search python /bench/architecture_warm.py
python3.11 - <<'PY'
from campaign import app_request,write,ROOT,container_state,archive
from architecture_processes import live_children,same_pool
import json,time
for _ in range(60):
    try:
        assert app_request('/bench/pipeline/warm-query','POST',120)['query_model_warmed'];break
    except Exception:time.sleep(2)
else:raise RuntimeError('Query warmup failed')
state=container_state()
pool=live_children(state['bench-search']['host_pid'])
assert len(pool)==4
write(ROOT/'results/aligned-configuration.json',{'containers':state,'worker_children':pool})
archive(ROOT/'results/aligned-configuration.json')
PY
docker stop -t 30 bench-search bench-dispatcher
echo aligned-server-ready
