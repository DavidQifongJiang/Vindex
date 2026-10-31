"""Commit ordering and rebalance buffers without a running broker."""
import importlib.util
from threading import Event
import unittest
from unittest.mock import MagicMock, patch

from app.workers import search_events as worker


@unittest.skipUnless(importlib.util.find_spec("confluent_kafka"), "optional Kafka client required")
class BatchWorkerTests(unittest.TestCase):
    def exercise(self, failure=False):
        from confluent_kafka import TopicPartition
        stop = Event()
        client = MagicMock()
        callbacks = {}
        def subscribe(topics, **kw):
            callbacks.update(kw)
        client.subscribe.side_effect = subscribe
        client.committed.side_effect = lambda *a, **kw: [TopicPartition("topic", 0, 0)]
        client.get_watermark_offsets.return_value = (0, 1)
        message = MagicMock()
        message.error.return_value = None
        message.topic.return_value = "topic"
        message.partition.return_value = 0
        message.offset.return_value = 0
        message.value.return_value = b"event"
        polls = 0
        def poll(timeout):
            nonlocal polls
            polls += 1
            if polls == 1:
                callbacks["on_assign"](client, [])
                return message
            if polls == 2:
                callbacks["on_revoke"](client, [])
                callbacks["on_assign"](client, [])
                # The new assignment redelivers the discarded, uncommitted row.
                return message
            stop.set()
            return None
        client.poll.side_effect = poll
        order = []
        client.commit.side_effect = lambda **kw: order.append(("commit", [p.offset for p in kw["offsets"]]))
        def consume(generation, rows):
            order.append(("db", rows))
            if failure:
                raise RuntimeError("rollback")
        with patch.object(worker, "consumer", return_value=client), patch.object(worker.repo, "starting_offset", return_value=0), \
                patch.object(worker.repo, "consume_batch", side_effect=consume) as apply:
            if failure:
                with self.assertRaises(RuntimeError):
                    worker.run_consumer("analytics-v1", stop)
            else:
                worker.run_consumer("analytics-v1", stop)
            apply.assert_called_once_with("analytics-v1", [("topic", 0, 0, b"event")])
        client.close.assert_called_once()
        return order

    def test_revocation_discards_buffer_and_db_precedes_commit(self):
        order = self.exercise()
        self.assertEqual(order[-1], ("commit", [1]))
        self.assertEqual(order[-2][0], "db")

    def test_database_failure_never_commits_processed_offsets(self):
        order = self.exercise(failure=True)
        self.assertEqual([offsets for kind, offsets in order if kind == "commit"], [[0], [0]])
