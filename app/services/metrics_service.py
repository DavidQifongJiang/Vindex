from datetime import datetime
from time import perf_counter, time
import logging
from botocore.exceptions import ClientError


def now_iso():
    return datetime.utcnow().isoformat()


def now_epoch():
    return time()


def seconds_since(start):
    return round(perf_counter() - start, 4)


def metrics_key(video_id):
    return f"metrics/{video_id}.json"


def read_metrics(storage, video_id):
    key = metrics_key(video_id)

    if not storage.exists(key):
        return {
            "video_id": video_id,
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "upload": {},
            "processing": {},
            "search": {},
        }

    return storage.read_json(key)


def write_metrics(storage, video_id, metrics):
    metrics["video_id"] = video_id
    metrics["updated_at"] = now_iso()
    metrics.setdefault("created_at", metrics["updated_at"])
    storage.write_json(metrics_key(video_id), metrics, indent=2)
    return metrics


def update_metrics(storage, video_id, section, values):
    for _ in range(8):
        metrics, etag = storage.read_json_versioned(metrics_key(video_id))
        metrics = metrics or {"video_id": video_id, "created_at": now_iso(), "upload": {}, "processing": {}, "search": {}}
        metrics.setdefault(section, {}).update(values)
        metrics["updated_at"] = now_iso()
        try:
            storage.write_json_conditional(metrics_key(video_id), metrics, etag)
            return metrics
        except ClientError as exc:
            if exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode") not in {404, 409, 412}:
                raise
    raise RuntimeError("Concurrent metrics updates exhausted conditional-write retries")


def safe_update_metrics(storage, video_id, section, values):
    try:
        return update_metrics(storage, video_id, section, values)
    except Exception:
        logging.getLogger(__name__).warning("Metrics update failed for %s", video_id, exc_info=True)
        return None
