import subprocess

import httpx
from botocore.exceptions import (
    ClientError,
    ConnectTimeoutError,
    ConnectionClosedError,
    EndpointConnectionError,
    ReadTimeoutError,
)
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse
from requests import exceptions as requests_exceptions
from sqlalchemy.exc import DisconnectionError, OperationalError


TEMPORARY_AWS_ERROR_CODES = {
    "RequestTimeout",
    "SlowDown",
    "Throttling",
    "ThrottlingException",
    "TooManyRequestsException",
}
TEMPORARY_HTTP_STATUS_CODES = {408, 429, 500, 502, 503, 504}
PERMANENT_AWS_NOT_FOUND_CODES = {"404", "NoSuchKey", "NotFound"}


class ProcessingError(Exception):
    default_error_code = "processing_error"
    retryable = False

    def __init__(self, message: str, *, error_code: str | None = None, cause: Exception | None = None):
        super().__init__(message)
        self.error_code = error_code or self.default_error_code
        self.cause = cause


class TemporaryProcessingError(ProcessingError):
    default_error_code = "temporary_processing_error"
    retryable = True


class PermanentProcessingError(ProcessingError):
    default_error_code = "permanent_processing_error"


class TemporaryStorageError(TemporaryProcessingError):
    default_error_code = "temporary_storage_error"


class PermanentStorageError(PermanentProcessingError):
    default_error_code = "permanent_storage_error"


class TemporaryDatabaseError(TemporaryProcessingError):
    default_error_code = "temporary_database_error"


class TemporaryVectorStoreError(TemporaryProcessingError):
    default_error_code = "temporary_vector_store_error"


class PermanentMediaError(PermanentProcessingError):
    default_error_code = "permanent_media_error"


class StaleProcessingAttempt(ProcessingError):
    default_error_code = "stale_processing_attempt"


def error_type(error: ProcessingError) -> str:
    return error.__class__.__name__


def client_error_code(error: ClientError) -> str:
    return str(error.response.get("Error", {}).get("Code", ""))


def client_error_status(error: ClientError) -> int | None:
    status = error.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    return int(status) if status else None


def classify_processing_exception(
    error: Exception,
    *,
    default_message: str = "Processing failed",
    default_error_code: str = "unexpected_processing_error",
) -> ProcessingError:
    if isinstance(error, ProcessingError):
        return error

    if isinstance(error, (OperationalError, DisconnectionError)):
        return TemporaryDatabaseError(
            "Database was temporarily unavailable",
            error_code="database_unavailable",
            cause=error,
        )

    if isinstance(error, (EndpointConnectionError, ConnectTimeoutError, ReadTimeoutError, ConnectionClosedError)):
        return TemporaryStorageError(
            "S3 was temporarily unavailable",
            error_code="s3_unavailable",
            cause=error,
        )

    if isinstance(error, ClientError):
        code = client_error_code(error)
        status = client_error_status(error)

        if code in PERMANENT_AWS_NOT_FOUND_CODES or status == 404:
            return PermanentStorageError(
                "Required S3 object was not found",
                error_code="s3_object_not_found",
                cause=error,
            )

        if code in TEMPORARY_AWS_ERROR_CODES or status in TEMPORARY_HTTP_STATUS_CODES:
            return TemporaryStorageError(
                "S3 returned a temporary error",
                error_code="s3_temporary_error",
                cause=error,
            )

        return PermanentStorageError(
            "S3 returned a non-retryable error",
            error_code="s3_permanent_error",
            cause=error,
        )

    if isinstance(error, ResponseHandlingException):
        return TemporaryVectorStoreError(
            "Qdrant connection failed",
            error_code="qdrant_unavailable",
            cause=error,
        )

    if isinstance(error, UnexpectedResponse):
        status = getattr(error, "status_code", None)
        if status in TEMPORARY_HTTP_STATUS_CODES:
            return TemporaryVectorStoreError(
                "Qdrant returned a temporary error",
                error_code="qdrant_temporary_error",
                cause=error,
            )

        return PermanentProcessingError(
            "Qdrant returned a non-retryable error",
            error_code="qdrant_permanent_error",
            cause=error,
        )

    if isinstance(error, (httpx.ConnectError, httpx.TimeoutException)):
        return TemporaryProcessingError(
            "Network dependency was temporarily unavailable",
            error_code="network_dependency_unavailable",
            cause=error,
        )

    if isinstance(error, (requests_exceptions.ConnectionError, requests_exceptions.Timeout)):
        return TemporaryProcessingError(
            "Model dependency was temporarily unavailable",
            error_code="model_dependency_unavailable",
            cause=error,
        )

    if isinstance(error, requests_exceptions.HTTPError):
        response = getattr(error, "response", None)
        status = getattr(response, "status_code", None)
        if status in TEMPORARY_HTTP_STATUS_CODES:
            return TemporaryProcessingError(
                "Model dependency returned a temporary error",
                error_code="model_dependency_temporary_error",
                cause=error,
            )

    if isinstance(error, subprocess.CalledProcessError):
        return PermanentMediaError(
            "FFmpeg could not process this video",
            error_code="unsupported_or_corrupt_media",
            cause=error,
        )

    return PermanentProcessingError(
        default_message,
        error_code=default_error_code,
        cause=error,
    )
