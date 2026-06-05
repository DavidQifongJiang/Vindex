from datetime import datetime
from time import perf_counter, time


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
    metrics = read_metrics(storage, video_id)
    metrics.setdefault(section, {})
    metrics[section].update(values)
    return write_metrics(storage, video_id, metrics)


def safe_update_metrics(storage, video_id, section, values):
    try:
        return update_metrics(storage, video_id, section, values)
    except Exception:
        return None
