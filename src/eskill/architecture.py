"""Architecture-layer profiles, adjustment, and runtime execution (timeout/retry/breaker/bulkhead)."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from dataclasses import dataclass, replace
from typing import Any

from .diagnostics import FaultDiagnosis, FaultType


@dataclass(slots=True)
class ArchitectureProfile:
    timeout_seconds: float = 30.0
    max_retries: int = 0
    backoff_factor: float = 1.5
    breaker_failure_threshold: int = 5
    breaker_recovery_seconds: float = 30.0
    bulkhead_max_concurrency: int = 64
    fallback_strategy: str = "fail"  # fail | echo_input | cached

    def to_dict(self) -> dict[str, Any]:
        return {
            "timeout_seconds": self.timeout_seconds,
            "max_retries": self.max_retries,
            "backoff_factor": self.backoff_factor,
            "breaker_failure_threshold": self.breaker_failure_threshold,
            "breaker_recovery_seconds": self.breaker_recovery_seconds,
            "bulkhead_max_concurrency": self.bulkhead_max_concurrency,
            "fallback_strategy": self.fallback_strategy,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> ArchitectureProfile:
        if not raw:
            return cls()
        return cls(
            timeout_seconds=float(raw.get("timeout_seconds") or 30.0),
            max_retries=int(raw.get("max_retries") or 0),
            backoff_factor=float(raw.get("backoff_factor") or 1.5),
            breaker_failure_threshold=int(raw.get("breaker_failure_threshold") or 5),
            breaker_recovery_seconds=float(raw.get("breaker_recovery_seconds") or 30.0),
            bulkhead_max_concurrency=int(raw.get("bulkhead_max_concurrency") or 64),
            fallback_strategy=str(raw.get("fallback_strategy") or "fail"),
        )


@dataclass
class _BreakerState:
    consecutive_failures: int = 0
    state: str = "closed"  # closed | open | half_open
    opened_at: float = 0.0


class ArchitectureAdjuster:
    """Compute next ArchitectureProfile from fault diagnosis."""

    @staticmethod
    def adjust(
        diagnosis: FaultDiagnosis,
        current: ArchitectureProfile | None,
    ) -> ArchitectureProfile:
        base = current or ArchitectureProfile()
        ft = diagnosis.fault_type

        if ft == FaultType.TIMEOUT:
            return replace(
                base,
                timeout_seconds=min(base.timeout_seconds * 1.5, 300.0),
                max_retries=min(base.max_retries + 1, 10),
            )
        if ft == FaultType.DEPENDENCY:
            return replace(
                base,
                breaker_failure_threshold=max(2, base.breaker_failure_threshold - 1),
                breaker_recovery_seconds=min(base.breaker_recovery_seconds * 1.2, 120.0),
                fallback_strategy="echo_input" if base.fallback_strategy == "fail" else base.fallback_strategy,
            )
        if ft == FaultType.CONCURRENCY:
            return replace(
                base,
                bulkhead_max_concurrency=max(1, base.bulkhead_max_concurrency // 2),
            )
        if ft == FaultType.RESOURCE_EXHAUSTION:
            return replace(
                base,
                bulkhead_max_concurrency=max(1, base.bulkhead_max_concurrency // 2),
                timeout_seconds=min(base.timeout_seconds * 1.2, 300.0),
            )
        if ft == FaultType.DEADLOCK:
            return replace(
                base,
                max_retries=1,
                bulkhead_max_concurrency=max(1, base.bulkhead_max_concurrency // 2),
            )
        return replace(base)

    @staticmethod
    def encode_into_logic(logic: dict[str, Any], profile: ArchitectureProfile) -> dict[str, Any]:
        out = dict(logic)
        out["architecture_profile"] = profile.to_dict()
        meta = dict(out.get("metadata") or {})
        res = dict(meta.get("resilience") or {})
        res.update(profile.to_dict())
        meta["resilience"] = res
        out["metadata"] = meta
        return out


class ArchitectureExecutor:
    """Cross-platform timeout, retries, circuit breaker, bulkhead, fallback."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._breakers: dict[str, _BreakerState] = {}
        self._semaphores: dict[str, threading.BoundedSemaphore] = {}

    def execute(
        self,
        skill_id: str,
        profile: ArchitectureProfile | None,
        fn: Callable[[], dict[str, Any]],
        *,
        input_data: dict[str, Any] | None = None,
        on_event: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        input_data = input_data or {}
        if profile is None:
            return fn()

        def emit(event_type: str, details: dict[str, Any]) -> None:
            if on_event:
                on_event(event_type, details)

        with self._lock:
            br = self._breakers.setdefault(skill_id, _BreakerState())
            sem_key = f"{skill_id}:{profile.bulkhead_max_concurrency}"
            if sem_key not in self._semaphores:
                self._semaphores[sem_key] = threading.BoundedSemaphore(profile.bulkhead_max_concurrency)
            sem = self._semaphores[sem_key]

        now = time.monotonic()
        if br.state == "open":
            if now - br.opened_at < profile.breaker_recovery_seconds:
                emit("architecture_breaker_open", {"skill_id": skill_id})
                return self._fallback(skill_id, profile, input_data, emit)
            br.state = "half_open"
            emit("architecture_breaker_half_open", {"skill_id": skill_id})

        if not sem.acquire(blocking=True, timeout=60.0):
            emit("architecture_bulkhead_timeout", {"skill_id": skill_id})
            return self._fallback(skill_id, profile, input_data, emit)

        try:
            attempts = profile.max_retries + 1
            last_exc: BaseException | None = None
            for attempt in range(attempts):
                try:
                    result = self._run_with_timeout(fn, profile.timeout_seconds)
                    br.consecutive_failures = 0
                    br.state = "closed"
                    return result
                except FuturesTimeout:
                    emit(
                        "architecture_timeout",
                        {"skill_id": skill_id, "attempt": attempt, "timeout": profile.timeout_seconds},
                    )
                    last_exc = TimeoutError(f"timeout after {profile.timeout_seconds}s")
                    br.consecutive_failures += 1
                except Exception as exc:
                    emit(
                        "architecture_execute_error",
                        {"skill_id": skill_id, "attempt": attempt, "error": str(exc)},
                    )
                    last_exc = exc
                    br.consecutive_failures += 1

                if br.consecutive_failures >= profile.breaker_failure_threshold:
                    br.state = "open"
                    br.opened_at = time.monotonic()
                    emit(
                        "architecture_breaker_open",
                        {"skill_id": skill_id, "failures": br.consecutive_failures},
                    )
                    return self._fallback(skill_id, profile, input_data, emit)

                if attempt < attempts - 1:
                    delay = profile.backoff_factor ** attempt
                    emit(
                        "architecture_retry",
                        {"skill_id": skill_id, "attempt": attempt + 1, "delay": delay},
                    )
                    time.sleep(min(delay, 30.0))

            if last_exc:
                raise last_exc
            raise RuntimeError("architecture_execute_failed")
        finally:
            sem.release()

    def _run_with_timeout(self, fn: Callable[[], dict[str, Any]], timeout_seconds: float) -> dict[str, Any]:
        with ThreadPoolExecutor(max_workers=1) as pool:
            fut = pool.submit(fn)
            return fut.result(timeout=timeout_seconds)

    def _fallback(
        self,
        skill_id: str,
        profile: ArchitectureProfile,
        input_data: dict[str, Any],
        emit: Callable[[str, dict[str, Any]], None],
    ) -> dict[str, Any]:
        emit(
            "architecture_fallback",
            {"skill_id": skill_id, "strategy": profile.fallback_strategy},
        )
        strat = profile.fallback_strategy
        if strat == "echo_input":
            return {"architecture_fallback": True, "echo": dict(input_data)}
        if strat == "cached":
            return {"architecture_fallback": True, "cached": None}
        raise RuntimeError(f"architecture_fallback_fail:{skill_id}")
