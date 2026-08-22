import os
import random
from datetime import datetime, timedelta
from typing import Callable


MAX_PROCESSING_ATTEMPTS = int(os.getenv("MAX_PROCESSING_ATTEMPTS", "3"))
RETRY_BACKOFF_BASE_SECONDS = int(os.getenv("PROCESSING_RETRY_BACKOFF_BASE_SECONDS", "30"))
RETRY_BACKOFF_CAP_SECONDS = int(os.getenv("PROCESSING_RETRY_BACKOFF_CAP_SECONDS", "600"))
RETRY_JITTER_RATIO = float(os.getenv("PROCESSING_RETRY_JITTER_RATIO", "0.2"))


def retry_delay_seconds(
    failed_attempt_count: int,
    *,
    base_seconds: int = RETRY_BACKOFF_BASE_SECONDS,
    cap_seconds: int = RETRY_BACKOFF_CAP_SECONDS,
    jitter_ratio: float = RETRY_JITTER_RATIO,
    jitter: Callable[[float, float], float] | None = None,
) -> int:
    exponent = max(failed_attempt_count - 1, 0)
    base_delay = min(cap_seconds, base_seconds * (2 ** exponent))

    if jitter_ratio <= 0:
        return int(base_delay)

    jitter_fn = jitter or random.uniform
    jitter_width = base_delay * jitter_ratio
    return max(0, int(round(base_delay + jitter_fn(-jitter_width, jitter_width))))


def next_retry_at(delay_seconds: int) -> datetime:
    return datetime.utcnow() + timedelta(seconds=delay_seconds)


def attempts_exhausted(attempt_count: int, max_attempts: int | None = None) -> bool:
    return attempt_count >= (max_attempts or MAX_PROCESSING_ATTEMPTS)
