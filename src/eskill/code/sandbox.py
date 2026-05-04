"""Subprocess-isolated execution for code skills."""

from __future__ import annotations

import json
import time
import traceback
from multiprocessing import Queue, get_context
from typing import Any

from .models import CodeSandboxResult

_SAFE_BUILTIN_NAMES: frozenset[str] = frozenset(
    {
        # Types / constructors
        "bool",
        "bytes",
        "bytearray",
        "complex",
        "dict",
        "float",
        "frozenset",
        "int",
        "list",
        "object",
        "set",
        "slice",
        "str",
        "tuple",
        "type",
        # Iteration / introspection
        "abs",
        "all",
        "any",
        "callable",
        "chr",
        "divmod",
        "enumerate",
        "filter",
        "format",
        "hasattr",
        "hash",
        "hex",
        "id",
        "isinstance",
        "issubclass",
        "iter",
        "len",
        "map",
        "max",
        "min",
        "next",
        "oct",
        "ord",
        "pow",
        "print",
        "range",
        "repr",
        "reversed",
        "round",
        "sorted",
        "sum",
        "zip",
        # Common exceptions (must be available for try/except inside skill)
        "ArithmeticError",
        "AssertionError",
        "AttributeError",
        "BaseException",
        "Exception",
        "IndexError",
        "KeyError",
        "LookupError",
        "NotImplementedError",
        "RuntimeError",
        "StopIteration",
        "TypeError",
        "ValueError",
        "ZeroDivisionError",
        # Singletons
        "True",
        "False",
        "None",
        "NotImplemented",
        "Ellipsis",
    }
)

# Modules that the validator already permits at import-time. We expose them
# via the allowed builtins ONLY so already-imported names work; we do not
# allow ``__import__`` itself.
_ALLOWED_IMPORT_MODULES: frozenset[str] = frozenset(
    {
        "json",
        "re",
        "math",
        "datetime",
        "collections",
        "itertools",
        "functools",
        "typing",
        "dataclasses",
        "copy",
    }
)


def _build_safe_builtins() -> dict[str, Any]:
    """Build a restricted ``__builtins__`` mapping for sandbox exec()."""
    import builtins as _b

    safe: dict[str, Any] = {}
    for name in _SAFE_BUILTIN_NAMES:
        if hasattr(_b, name):
            safe[name] = getattr(_b, name)

    # Allow ``import`` *only* for whitelisted modules so user code can do
    # ``import json`` if needed; everything else raises ImportError.
    real_import = _b.__import__

    def _restricted_import(name, globals=None, locals=None, fromlist=(), level=0):  # type: ignore[no-untyped-def]
        root = (name or "").split(".")[0]
        if level and level > 0:
            raise ImportError("relative imports are not allowed in sandbox")
        if root not in _ALLOWED_IMPORT_MODULES:
            raise ImportError(f"import of {name!r} is not allowed in sandbox")
        return real_import(name, globals, locals, fromlist, level)

    safe["__import__"] = _restricted_import
    return safe


def _limit_memory_mb(max_mb: int) -> None:
    try:
        import resource

        max_bytes = max_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (max_bytes, max_bytes))
    except Exception:
        pass


def _sandbox_worker_entry(payload: dict[str, Any]) -> dict[str, Any]:
    """Runs inside child process; must stay picklable at module level."""
    import json as _json

    t0 = time.perf_counter()
    source = str(payload.get("source_code") or "")
    fn_name = str(payload.get("function_name") or "run")
    input_data = dict(payload.get("input_data") or {})
    max_mem = int(payload.get("max_memory_mb") or 128)

    _limit_memory_mb(max_mem)

    try:
        safe_builtins = _build_safe_builtins()
        ns: dict[str, Any] = {"__builtins__": safe_builtins}
        exec(compile(source, "<sandbox>", "exec"), ns, ns)
        fn = ns.get(fn_name)
        if not callable(fn):
            return {
                "success": False,
                "output": {},
                "error_type": "RuntimeError",
                "error_message": f"function {fn_name!r} not found or not callable",
                "traceback_str": "",
                "duration_ms": round((time.perf_counter() - t0) * 1000, 3),
            }
        result = fn(**input_data)
        out_dict = result if isinstance(result, dict) else {"result": result}
        raw = _json.dumps(out_dict, default=str)
        max_out = int(payload.get("max_output_size") or 10000)
        if len(raw) > max_out:
            return {
                "success": False,
                "output": {},
                "error_type": "ValueError",
                "error_message": f"output_json_too_large:{len(raw)}>{max_out}",
                "traceback_str": "",
                "duration_ms": round((time.perf_counter() - t0) * 1000, 3),
            }
        return {
            "success": True,
            "output": out_dict,
            "error_type": "",
            "error_message": "",
            "traceback_str": "",
            "duration_ms": round((time.perf_counter() - t0) * 1000, 3),
        }
    except Exception as exc:
        return {
            "success": False,
            "output": {},
            "error_type": type(exc).__name__,
            "error_message": str(exc),
            "traceback_str": traceback.format_exc(),
            "duration_ms": round((time.perf_counter() - t0) * 1000, 3),
        }


def _process_target(q: Queue, payload: dict[str, Any]) -> None:
    try:
        q.put(_sandbox_worker_entry(payload))
    except Exception as exc:
        q.put(
            {
                "success": False,
                "output": {},
                "error_type": type(exc).__name__,
                "error_message": str(exc),
                "traceback_str": traceback.format_exc(),
                "duration_ms": 0.0,
            }
        )


class CodeSandbox:
    """Run validated Python in a spawned subprocess."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 5.0,
        max_memory_mb: int = 128,
        max_output_size: int = 10000,
    ):
        self.timeout_seconds = timeout_seconds
        self.max_memory_mb = max_memory_mb
        self.max_output_size = max_output_size

    def execute(
        self,
        source_code: str,
        function_name: str,
        input_data: dict[str, Any],
        *,
        timeout_seconds: float | None = None,
        max_memory_mb: int | None = None,
        max_output_size: int | None = None,
    ) -> CodeSandboxResult:
        timeout = timeout_seconds if timeout_seconds is not None else self.timeout_seconds
        mem = max_memory_mb if max_memory_mb is not None else self.max_memory_mb
        max_out = max_output_size if max_output_size is not None else self.max_output_size

        # Ensure JSON-serializable inputs for subprocess clarity
        try:
            json.dumps(input_data, default=str)
        except (TypeError, ValueError) as exc:
            return CodeSandboxResult(
                success=False,
                output={},
                error_type=type(exc).__name__,
                error_message=str(exc),
                traceback_str="",
            )

        ctx = get_context("spawn")
        q: Queue = ctx.Queue()
        payload: dict[str, Any] = {
            "source_code": source_code,
            "function_name": function_name,
            "input_data": input_data,
            "max_memory_mb": mem,
            "max_output_size": max_out,
        }
        proc = ctx.Process(target=_process_target, args=(q, payload))
        proc.start()
        proc.join(timeout=timeout)
        if proc.is_alive():
            proc.terminate()
            proc.join(timeout=2)
            return CodeSandboxResult(
                success=False,
                output={},
                error_type="TimeoutError",
                error_message=f"execution timed out after {timeout}s",
                traceback_str="",
            )
        try:
            raw = q.get_nowait()
        except Exception:
            return CodeSandboxResult(
                success=False,
                output={},
                error_type="RuntimeError",
                error_message="sandbox_empty_queue",
                traceback_str="",
            )
        return CodeSandboxResult(
            success=bool(raw.get("success")),
            output=dict(raw.get("output") or {}),
            error_type=str(raw.get("error_type") or ""),
            error_message=str(raw.get("error_message") or ""),
            traceback_str=str(raw.get("traceback_str") or ""),
            duration_ms=float(raw.get("duration_ms") or 0.0),
        )
