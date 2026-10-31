import unittest
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy.exc import OperationalError

from app.services.processing_errors import PermanentMediaError, TemporaryStorageError
from app.workers.task import run_processing_task


class RetryScheduled(Exception):
    pass


class FakeDb:
    def close(self):
        pass


class FakeTask:
    def __init__(self, retries=0):
        self.request = SimpleNamespace(
            hostname="worker-1",
            id="task-1",
            retries=retries,
        )
        self.retry_call = None

    def retry(self, *, exc, countdown):
        self.retry_call = {
            "exc": exc,
            "countdown": countdown,
        }
        raise RetryScheduled()


def claimed_video(attempt_count=1):
    return SimpleNamespace(
        processing_token="token-1",
        processing_attempt_count=attempt_count,
        max_processing_attempts=3,
    )


class ProcessingTaskRetryTest(unittest.TestCase):
    @patch("app.workers.task.create_session", return_value=FakeDb())
    @patch("app.workers.task.claim_video_for_processing", return_value=None)
    @patch("app.workers.task.process_video")
    def test_duplicate_delivery_exits_when_claim_fails(
        self,
        process_video,
        claim_video_for_processing,
        create_session,
    ):
        result = run_processing_task(FakeTask(), "video-1")

        self.assertEqual(result["status"], "skipped")
        process_video.assert_not_called()

    @patch("app.workers.task.retry_delay_seconds", return_value=42)
    @patch("app.workers.task.create_session", return_value=FakeDb())
    @patch("app.workers.task.claim_video_for_processing")
    @patch("app.workers.task.process_video")
    def test_temporary_claim_failure_schedules_retry_without_processing(
        self,
        process_video,
        claim_video_for_processing,
        create_session,
        retry_delay_seconds,
    ):
        task = FakeTask()
        claim_video_for_processing.side_effect = OperationalError(
            "select 1",
            {},
            Exception("database unavailable"),
        )

        with self.assertRaises(RetryScheduled):
            run_processing_task(task, "video-1")

        self.assertEqual(task.retry_call["countdown"], 42)
        process_video.assert_not_called()

    @patch("app.workers.task.retry_delay_seconds", return_value=42)
    @patch("app.workers.task.record_processing_retry", return_value=object())
    @patch("app.workers.task.create_session", return_value=FakeDb())
    @patch("app.workers.task.claim_video_for_processing", return_value=claimed_video(1))
    @patch("app.workers.task.process_video")
    def test_temporary_failure_marks_retrying_and_schedules_retry(
        self,
        process_video,
        claim_video_for_processing,
        create_session,
        record_processing_retry,
        retry_delay_seconds,
    ):
        task = FakeTask()
        process_video.side_effect = TemporaryStorageError(
            "S3 was temporarily unavailable",
            error_code="s3_unavailable",
        )

        with self.assertRaises(RetryScheduled):
            run_processing_task(task, "video-1")

        self.assertEqual(task.retry_call["countdown"], 42)
        record_processing_retry.assert_called_once()

    @patch("app.workers.task.record_processing_failure", return_value=object())
    @patch("app.workers.task.create_session", return_value=FakeDb())
    @patch("app.workers.task.claim_video_for_processing", return_value=claimed_video(3))
    @patch("app.workers.task.process_video")
    def test_temporary_failure_on_third_attempt_marks_failed(
        self,
        process_video,
        claim_video_for_processing,
        create_session,
        record_processing_failure,
    ):
        task = FakeTask(retries=2)
        process_video.side_effect = TemporaryStorageError(
            "S3 was temporarily unavailable",
            error_code="s3_unavailable",
        )

        with self.assertRaises(TemporaryStorageError):
            run_processing_task(task, "video-1")

        self.assertIsNone(task.retry_call)
        record_processing_failure.assert_called_once()

    @patch("app.workers.task.record_processing_failure", return_value=object())
    @patch("app.workers.task.create_session", return_value=FakeDb())
    @patch("app.workers.task.claim_video_for_processing", return_value=claimed_video(1))
    @patch("app.workers.task.process_video")
    def test_permanent_failure_marks_failed_without_retry(
        self,
        process_video,
        claim_video_for_processing,
        create_session,
        record_processing_failure,
    ):
        task = FakeTask()
        process_video.side_effect = PermanentMediaError(
            "FFmpeg could not process this video",
            error_code="unsupported_or_corrupt_media",
        )

        with self.assertRaises(PermanentMediaError):
            run_processing_task(task, "video-1")

        self.assertIsNone(task.retry_call)
        record_processing_failure.assert_called_once()


if __name__ == "__main__":
    unittest.main()
