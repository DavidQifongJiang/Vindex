import subprocess
import unittest

from botocore.exceptions import ClientError, EndpointConnectionError
from sqlalchemy.exc import OperationalError

from app.services.processing_errors import (
    PermanentMediaError,
    PermanentProcessingError,
    PermanentStorageError,
    TemporaryDatabaseError,
    TemporaryStorageError,
    classify_processing_exception,
)


def client_error(code: str, status: int):
    return ClientError(
        {
            "Error": {
                "Code": code,
                "Message": "test error",
            },
            "ResponseMetadata": {
                "HTTPStatusCode": status,
            },
        },
        "GetObject",
    )


class ProcessingErrorClassificationTest(unittest.TestCase):
    def test_s3_connection_error_is_temporary(self):
        error = classify_processing_exception(
            EndpointConnectionError(endpoint_url="https://s3.example.test")
        )

        self.assertIsInstance(error, TemporaryStorageError)
        self.assertEqual(error.error_code, "s3_unavailable")

    def test_s3_slowdown_is_temporary(self):
        error = classify_processing_exception(client_error("SlowDown", 503))

        self.assertIsInstance(error, TemporaryStorageError)
        self.assertEqual(error.error_code, "s3_temporary_error")

    def test_s3_not_found_is_permanent(self):
        error = classify_processing_exception(client_error("NoSuchKey", 404))

        self.assertIsInstance(error, PermanentStorageError)
        self.assertEqual(error.error_code, "s3_object_not_found")

    def test_database_operational_error_is_temporary(self):
        error = classify_processing_exception(
            OperationalError("select 1", {}, Exception("connection lost"))
        )

        self.assertIsInstance(error, TemporaryDatabaseError)
        self.assertEqual(error.error_code, "database_unavailable")

    def test_ffmpeg_failure_is_permanent_media_error(self):
        error = classify_processing_exception(
            subprocess.CalledProcessError(returncode=1, cmd=["ffmpeg"])
        )

        self.assertIsInstance(error, PermanentMediaError)
        self.assertEqual(error.error_code, "unsupported_or_corrupt_media")

    def test_unknown_exception_is_permanent(self):
        error = classify_processing_exception(RuntimeError("bug"))

        self.assertIsInstance(error, PermanentProcessingError)
        self.assertEqual(error.error_code, "unexpected_processing_error")


if __name__ == "__main__":
    unittest.main()
