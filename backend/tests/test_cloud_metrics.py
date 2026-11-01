from unittest.mock import Mock, patch
import unittest

from app.db.models import PipelineJob, VideoSearchChunk
from app.db.pipeline_repository import add_job
from app.workers.cloud_metrics import metric_data, pipeline_backlog, publish_once, worker_task_counts
from tests import test_durable_pipeline as durable


class CloudMetricsTest(unittest.TestCase):
    database_url = "sqlite:///:memory:"
    setUp = durable.DurablePipelineTest.setUp
    cleanup = durable.DurablePipelineTest.cleanup
    chunks = durable.DurablePipelineTest.chunks

    def set_status(self, model, status, **filters):
        self.db.query(model).filter_by(**filters).update({"status": status})
        self.db.commit()

    def test_backlog_counts_active_work_per_queue(self):
        add_job(self.db, "v1", "search")
        add_job(self.db, "v1", "playback")
        add_job(self.db, "v1", "finalize")
        self.db.commit()
        self.chunks(3)
        self.assertEqual(pipeline_backlog(self.db), {"search": 5, "playback": 1})

        self.set_status(VideoSearchChunk, "completed", chunk_index=0)
        self.set_status(VideoSearchChunk, "processing", chunk_index=1)
        self.set_status(PipelineJob, "completed", stage="search")
        self.set_status(PipelineJob, "failed", stage="playback")
        self.assertEqual(pipeline_backlog(self.db), {"search": 3, "playback": 0})

    def test_metric_data_signals_cold_start_and_per_task_load(self):
        data = metric_data({"search": 6, "playback": 0}, {"search": 0, "playback": 2},
                           {"pending_age_seconds": 12, "expired_work": 1, "unrelated": 5})
        values = {(item["MetricName"], (item.get("Dimensions") or [{"Value": None}])[0]["Value"]): item["Value"]
                  for item in data}
        self.assertEqual(values[("Backlog", "search")], 6)
        self.assertEqual(values[("BacklogPerTask", "search")], 6)
        self.assertEqual(values[("WorkerNeeded", "search")], 1)
        self.assertEqual(values[("WorkerNeeded", "playback")], 0)
        self.assertEqual(values[("BacklogPerTask", "playback")], 0)
        self.assertEqual(values[("pending_age_seconds", None)], 12.0)
        self.assertNotIn(("unrelated", None), values)

    def test_task_counts_include_pending_tasks(self):
        ecs = Mock()
        ecs.describe_services.return_value = {"services": [
            {"serviceName": "vindex-search-worker", "runningCount": 1, "pendingCount": 1}]}
        counts = worker_task_counts(ecs, "vindex", {"search": "vindex-search-worker", "playback": None})
        self.assertEqual(counts, {"search": 2})
        self.assertEqual(worker_task_counts(None, None, {"search": "x"}), {})

    def test_publish_once_sends_metrics(self):
        add_job(self.db, "v1", "playback")
        self.db.commit()
        cloudwatch = Mock()
        with patch("app.workers.cloud_metrics.create_session", self.Session), \
             patch("app.workers.maintenance.operational_metrics", return_value={"expired_work": 0}):
            self.assertEqual(publish_once(cloudwatch), {"search": 0, "playback": 1})
        sent = cloudwatch.put_metric_data.call_args.kwargs
        self.assertEqual(sent["Namespace"], "Vindex/Pipeline")
        self.assertIn("Backlog", {item["MetricName"] for item in sent["MetricData"]})


if __name__ == "__main__":
    unittest.main()
