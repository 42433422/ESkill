from __future__ import annotations

import logging
import os
import sys
from typing import Any


def get_logger(name: str, level: str | int | None = None) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(StructuredFormatter())
        logger.addHandler(handler)
    if level is not None:
        logger.setLevel(level if isinstance(level, int) else getattr(logging, level.upper()))
    return logger


class StructuredFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        level = record.levelname
        timestamp = self.formatTime(record, "%Y-%m-%d %H:%M:%S")
        message = record.getMessage()
        extra = ""
        if hasattr(record, "skill_id"):
            extra += f" skill_id={record.skill_id}"
        if hasattr(record, "run_id"):
            extra += f" run_id={record.run_id}"
        if hasattr(record, "version"):
            extra += f" version={record.version}"
        if hasattr(record, "stage"):
            extra += f" stage={record.stage}"
        return f"[{timestamp}] {level:8} eskill {extra} - {message}"


class LogContext:
    def __init__(self, logger: logging.Logger, **kwargs: Any):
        self.logger = logger
        self.context = kwargs

    def info(self, msg: str, **extra: Any) -> None:
        self._log(logging.INFO, msg, extra)

    def warning(self, msg: str, **extra: Any) -> None:
        self._log(logging.WARNING, msg, extra)

    def error(self, msg: str, **extra: Any) -> None:
        self._log(logging.ERROR, msg, extra)

    def debug(self, msg: str, **extra: Any) -> None:
        self._log(logging.DEBUG, msg, extra)

    def _log(self, level: int, msg: str, extra: dict[str, Any]) -> None:
        record = self.logger.makeRecord(
            self.logger.name, level, "", 0, msg, (), None
        )
        for k, v in self.context.items():
            setattr(record, k, v)
        for k, v in extra.items():
            setattr(record, k, v)
        self.logger.handle(record)


def make_context(logger: logging.Logger, **kwargs: Any) -> LogContext:
    return LogContext(logger, **kwargs)


_logger = get_logger("eskill", os.getenv("ESKILL_LOG_LEVEL", "INFO"))


def log_skill_run(skill_id: str, run_id: str, stage: str, msg: str, **extra: Any) -> None:
    ctx = make_context(_logger, skill_id=skill_id, run_id=run_id, stage=stage)
    ctx.info(msg, **extra)


def log_error(skill_id: str, run_id: str, error: str, **extra: Any) -> None:
    ctx = make_context(_logger, skill_id=skill_id, run_id=run_id)
    ctx.error(error, **extra)


def log_version_solidified(skill_id: str, run_id: str, version: int, **extra: Any) -> None:
    ctx = make_context(_logger, skill_id=skill_id, run_id=run_id, version=version)
    ctx.info(f"Version {version} solidified", **extra)
