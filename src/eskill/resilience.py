from __future__ import annotations

import time
import functools
import logging
from typing import Any, Callable, TypeVar

from .errors import ESkillError, ESkillErrorCode


logger = logging.getLogger(__name__)

T = TypeVar("T")


class RetryPolicy:
    def __init__(
        self,
        max_retries: int = 3,
        backoff_factor: float = 1.0,
        max_backoff: float = 30.0,
        retryable_errors: list[ESkillErrorCode] | None = None,
    ):
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor
        self.max_backoff = max_backoff
        self.retryable_errors = retryable_errors or [
            ESkillErrorCode.LLM_API_ERROR,
            ESkillErrorCode.LLM_TIMEOUT,
            ESkillErrorCode.LLM_RATE_LIMITED,
            ESkillErrorCode.STORE_READ_FAILED,
            ESkillErrorCode.STORE_WRITE_FAILED,
        ]

    def execute(self, func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                return func(*args, **kwargs)
            except ESkillError as e:
                if e.code not in self.retryable_errors:
                    raise
                last_error = e
                if attempt < self.max_retries:
                    wait = min(
                        self.backoff_factor * (2 ** attempt),
                        self.max_backoff,
                    )
                    logger.warning(
                        f"Retry {attempt + 1}/{self.max_retries} after {wait:.1f}s: {e}"
                    )
                    time.sleep(wait)
            except Exception as e:
                last_error = e
                if attempt < self.max_retries:
                    wait = min(
                        self.backoff_factor * (2 ** attempt),
                        self.max_backoff,
                    )
                    logger.warning(
                        f"Retry {attempt + 1}/{self.max_retries} after {wait:.1f}s: {e}"
                    )
                    time.sleep(wait)
        raise last_error


class TimeoutHandler:
    def __init__(self, timeout_seconds: float = 30.0):
        self.timeout_seconds = timeout_seconds

    def execute(self, func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        import signal

        def handler(signum, frame):
            raise TimeoutError(f"Operation timed out after {self.timeout_seconds}s")

        signal.signal(signal.SIGALRM, handler)
        signal.alarm(int(self.timeout_seconds))
        try:
            return func(*args, **kwargs)
        finally:
            signal.alarm(0)


class FallbackStrategy:
    def __init__(self, fallback_func: Callable[..., T] | None = None):
        self.fallback_func = fallback_func

    def execute(
        self, func: Callable[..., T], *args: Any, fallback: Callable[..., T] | None = None, **kwargs: Any
    ) -> T:
        try:
            return func(*args, **kwargs)
        except ESkillError as e:
            logger.warning(f"Fallback triggered: {e}")
            fb = fallback or self.fallback_func
            if fb is None:
                raise
            return fb(*args, **kwargs)


class ResiliencePolicy:
    """Combined retry + timeout + fallback policy."""

    def __init__(
        self,
        retry_policy: RetryPolicy | None = None,
        timeout_seconds: float | None = None,
        fallback_func: Callable[..., Any] | None = None,
    ):
        self.retry_policy = retry_policy or RetryPolicy()
        self.timeout_seconds = timeout_seconds
        self.fallback = FallbackStrategy(fallback_func) if fallback_func else None

    def execute(self, func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        def wrapped():
            return func(*args, **kwargs)

        # 先尝试执行（带重试）
        if self.fallback:
            try:
                if self.timeout_seconds:
                    handler = TimeoutHandler(self.timeout_seconds)
                    return handler.execute(wrapped)
                return self.retry_policy.execute(wrapped)
            except Exception as e:
                logger.warning(f"Fallback triggered: {e}")
                return self.fallback.execute(wrapped)

        # 无 fallback，直接执行
        if self.timeout_seconds:
            handler = TimeoutHandler(self.timeout_seconds)
            return handler.execute(wrapped)
        return self.retry_policy.execute(wrapped)
