"""Publish pipeline backlog to CloudWatch so ECS can scale workers from zero.

Postgres owns durable job state, so backlog is counted there rather than from
Redis: the dispatcher republishes unclaimed work, which makes Redis queue
length overcount. Work that is processing still counts, so scale-in waits until
nothing is in flight.

Per queue (dimension ``Queue``):
- ``Backlog``: active work items.
- ``BacklogPerTask``: backlog divided by running + pending tasks; the target
  tracking metric.
- ``WorkerNeeded``: 1 when work is waiting and no task exists; drives a fast
  0 -> 1 scale-out so the first upload doesn't wait for target tracking.

Pipeline health values from the maintenance process are published too, for alarms.
"""
import logging
import os
from threading import Event

from sqlalchemy import func

from app.db.models import PipelineJob, VideoSearchChunk
from app.db.session import create_session

logger = logging.getLogger(__name__)

NAMESPACE = os.getenv("PIPELINE_METRICS_NAMESPACE", "Vindex/Pipeline")
ACTIVE_STATUSES = ("pending", "retrying", "processing")
QUEUE_STAGES = {"search": ("search", "finalize"), "playback": ("playback",)}
HEALTH_METRICS = ("pending_age_seconds", "expired_work", "recent_failures", "dispatcher_success_age_seconds")


def pipeline_backlog(db):
    stage_counts = dict(db.query(PipelineJob.stage, func.count()).filter(
        PipelineJob.status.in_(ACTIVE_STATUSES)).group_by(PipelineJob.stage).all())
    chunk_count = db.query(func.count()).select_from(VideoSearchChunk).filter(
        VideoSearchChunk.status.in_(ACTIVE_STATUSES)).scalar() or 0
    backlog = {queue: sum(stage_counts.get(stage, 0) for stage in stages) for queue, stages in QUEUE_STAGES.items()}
    backlog["search"] += chunk_count
    return backlog


def worker_task_counts(ecs, cluster, services):
    """Running + pending task count per queue, or {} when ECS isn't configured."""
    names = {queue: name for queue, name in services.items() if name}
    if not (ecs and cluster and names):
        return {}
    described = ecs.describe_services(cluster=cluster, services=list(names.values()))["services"]
    by_name = {service["serviceName"]: service["runningCount"] + service["pendingCount"] for service in described}
    return {queue: by_name.get(name, 0) for queue, name in names.items()}


def metric_data(backlog, tasks, health=None):
    data = []
    for queue, count in backlog.items():
        dimensions = [{"Name": "Queue", "Value": queue}]
        data.append({"MetricName": "Backlog", "Dimensions": dimensions, "Value": count, "Unit": "Count"})
        if queue in tasks:
            data.append({"MetricName": "BacklogPerTask", "Dimensions": dimensions,
                         "Value": count / max(tasks[queue], 1), "Unit": "Count"})
            data.append({"MetricName": "WorkerNeeded", "Dimensions": dimensions,
                         "Value": 1 if count and not tasks[queue] else 0, "Unit": "Count"})
    for name in HEALTH_METRICS:
        if health and name in health:
            data.append({"MetricName": name, "Value": float(health[name]),
                         "Unit": "Seconds" if name.endswith("_seconds") else "Count"})
    return data


def publish_once(cloudwatch, ecs=None, *, cluster=None, services=None):
    from app.workers.maintenance import operational_metrics

    db = create_session()
    try:
        backlog = pipeline_backlog(db)
        health = operational_metrics(db)
    finally:
        db.close()
    data = metric_data(backlog, worker_task_counts(ecs, cluster, services or {}), health)
    for start in range(0, len(data), 1000):
        cloudwatch.put_metric_data(Namespace=NAMESPACE, MetricData=data[start:start + 1000])
    return backlog


def main():
    import boto3

    logging.basicConfig(level=logging.INFO)
    cloudwatch, ecs = boto3.client("cloudwatch"), boto3.client("ecs")
    cluster = os.getenv("ECS_CLUSTER")
    services = {"search": os.getenv("SEARCH_WORKER_SERVICE"), "playback": os.getenv("PLAYBACK_WORKER_SERVICE")}
    interval = max(10, int(os.getenv("PIPELINE_METRICS_INTERVAL_SECONDS", "60")))
    stop = Event()
    while not stop.is_set():
        try:
            logger.info("Published backlog %s", publish_once(cloudwatch, ecs, cluster=cluster, services=services))
        except Exception:
            logger.exception("Publishing pipeline metrics failed")
        stop.wait(interval)


if __name__ == "__main__":
    main()
