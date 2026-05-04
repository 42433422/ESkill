"""Failure diagnosis for code-layer execution."""

from __future__ import annotations

import re
import traceback
from typing import Any

from .models import CodeDiagnosis


class CodeDiagnostics:
    """Analyze code failures for patch generation context."""

    def diagnose(
        self,
        source_code: str,
        function_name: str,
        input_data: dict[str, Any],
        error: BaseException | None,
        *,
        traceback_str: str | None = None,
        local_variables: dict[str, Any] | None = None,
    ) -> CodeDiagnosis:
        if error is None:
            return CodeDiagnosis(
                error_type="",
                error_message="",
                traceback_str="",
                failing_line="",
                local_variables={},
                suggested_fix_type="unknown",
            )
        tb = traceback_str if traceback_str is not None else traceback.format_exception(type(error), error, error.__traceback__)
        tb_str = "".join(tb) if isinstance(tb, list) else str(tb)
        failing_line = self._extract_failing_line(source_code, tb_str)
        return CodeDiagnosis(
            error_type=type(error).__name__,
            error_message=str(error),
            traceback_str=tb_str,
            failing_line=failing_line,
            local_variables=dict(local_variables or {}),
            suggested_fix_type=self._classify_error(error),
        )

    def _extract_failing_line(self, source_code: str, traceback_str: str) -> str:
        lines = source_code.splitlines()
        # Match "File \"...\", line N"
        for m in re.finditer(r'File "[^"]+", line (\d+)', traceback_str):
            try:
                lineno = int(m.group(1))
                if 1 <= lineno <= len(lines):
                    return lines[lineno - 1].strip()
            except ValueError:
                continue
        return ""

    def _classify_error(self, error: BaseException) -> str:
        if isinstance(error, KeyError):
            return "missing_key"
        if isinstance(error, TypeError):
            return "type_mismatch"
        if isinstance(error, ValueError):
            return "invalid_value"
        if isinstance(error, AttributeError):
            return "missing_attribute"
        if isinstance(error, IndexError):
            return "index_out_of_range"
        if isinstance(error, TimeoutError):
            return "timeout"
        return "unknown"
