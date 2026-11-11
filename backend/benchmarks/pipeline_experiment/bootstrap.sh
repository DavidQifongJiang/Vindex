#!/bin/bash
set -euo pipefail
cd /opt/vindex-experiment
source runtime.env
chmod 600 app.env postgres.env private.json runtime.env
mkdir -p results tls
openssl req -x509 -newkey rsa:2048 -keyout tls/key.pem -out tls/cert.pem -sha256 -days 3 -nodes -subj /CN=bench-proxy >/dev/null 2>&1
docker network create pipeline-experiment
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin 585572506964.dkr.ecr.us-east-1.amazonaws.com
for image in "$APP_IMAGE" "$POSTGRES_IMAGE" "$REDIS_IMAGE" "$QDRANT_IMAGE" "$PROXY_IMAGE" "$BROWSER_IMAGE"; do docker pull "$image"; done
docker run -d --name bench-postgres --network pipeline-experiment --cpus=.5 --memory=768m --memory-swap=768m --env-file postgres.env -v pipeline-postgres:/var/lib/postgresql/data "$POSTGRES_IMAGE"
docker run -d --name bench-redis --network pipeline-experiment --cpus=.25 --memory=256m --memory-swap=256m "$REDIS_IMAGE" redis-server --appendonly yes
docker run -d --name bench-qdrant --network pipeline-experiment --cpus=.5 --memory=1g --memory-swap=1g -v pipeline-qdrant:/qdrant/storage "$QDRANT_IMAGE"
for i in $(seq 1 60); do docker exec bench-postgres pg_isready -U bench -d vindex_pipeline_bench && break; sleep 2; done
docker run --rm --network pipeline-experiment --cpus=.5 --memory=1g --env-file app.env "$APP_IMAGE" alembic upgrade head
docker run -d --name bench-api --network pipeline-experiment --cpus=.5 --memory=2g --memory-swap=2g --env-file app.env -v /opt/vindex-experiment:/bench:ro "$APP_IMAGE" uvicorn server:application --host 0.0.0.0 --port 8000 --no-access-log
cat > Caddyfile <<'CADDY'
:8443 {
  tls /tls/cert.pem /tls/key.pem
  reverse_proxy bench-api:8000
}
CADDY
docker run -d --name bench-proxy --network pipeline-experiment --cpus=.5 --memory=128m --memory-swap=128m -p 127.0.0.1:8443:8443 -v /opt/vindex-experiment/Caddyfile:/etc/caddy/Caddyfile:ro -v /opt/vindex-experiment/tls:/tls:ro "$PROXY_IMAGE"
docker run -d --name bench-dispatcher --network pipeline-experiment --cpus=.25 --memory=512m --memory-swap=512m --env-file app.env "$APP_IMAGE" python -m app.workers.pipeline_dispatcher
docker run -d --name bench-search --network pipeline-experiment --cpus=2 --memory=4g --memory-swap=4g --env-file app.env -e OMP_NUM_THREADS=2 -e MKL_NUM_THREADS=2 -e OPENBLAS_NUM_THREADS=2 -e NUMEXPR_NUM_THREADS=2 "$APP_IMAGE" celery -A app.workers.celery_app.celery_app worker --loglevel=info --pool=prefork --concurrency=1 -Q search,celery -n search@experiment
docker run -d --name bench-playback --network pipeline-experiment --cpus=1 --memory=2g --memory-swap=2g --env-file app.env -e OMP_NUM_THREADS=1 -e MKL_NUM_THREADS=1 -e OPENBLAS_NUM_THREADS=1 -e NUMEXPR_NUM_THREADS=1 "$APP_IMAGE" celery -A app.workers.celery_app.celery_app worker --loglevel=info --pool=prefork --concurrency=1 -Q playback -n playback@experiment
docker run -d --init --name bench-browser --network pipeline-experiment --shm-size=1g --cpus=2 --memory=4g --memory-swap=4g -v /opt/vindex-experiment:/bench -w /bench "$BROWSER_IMAGE" sleep infinity
docker exec bench-browser npm install --no-audit --no-fund --prefix /bench/runtime node@22.23.4
python3.11 - <<'PY'
import json
from pathlib import Path
p=json.loads(Path('private.json').read_text())
Path('session.json').write_text(json.dumps({'cookies':[{'name':'vindex_bench','value':p['token'],'domain':'bench-proxy','path':'/','expires':-1,'httpOnly':True,'secure':True,'sameSite':'Lax'}],'origins':[]}))
PY
chmod 600 session.json
cat > /etc/systemd/system/pipeline-campaign.service <<'UNIT'
[Unit]
Description=Isolated Vindex pipeline experiment
After=docker.service
[Service]
Type=simple
WorkingDirectory=/opt/vindex-experiment
ExecStart=/usr/bin/python3.11 /opt/vindex-experiment/campaign.py --plan /opt/vindex-experiment/plan.json
StandardOutput=append:/opt/vindex-experiment/results/console.log
StandardError=append:/opt/vindex-experiment/results/console.log
TimeoutStopSec=15
UNIT
systemctl daemon-reload
python3.11 campaign.py --inventory
echo pipeline-host-ready
