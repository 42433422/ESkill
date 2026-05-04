"""Isolated subprocess validation for candidate static_logic."""

from __future__ import annotations

from dataclasses import dataclass, field
from multiprocessing import Queue, get_context
from typing import Any

from .static_executor import execute_static_logic, quality_report


def _sandbox_worker_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Top-level entry for spawn — must stay importable."""
    logic = payload["logic"]
    gate_default = payload.get("gate") or {}
    cases: list[dict[str, Any]] = list(payload.get("cases") or [])
    results: list[dict[str, Any]] = []
    for case in cases:
        case_id = str(case.get("case_id") or case.get("id") or "case")
        inp = dict(case.get("input_data") or {})
        gate = dict(case.get("quality_gate") or gate_default)
        try:
            out = execute_static_logic(logic, inp)
            q = quality_report(out, gate)
            partial = dict(case.get("assert_partial_output") or {})
            partial_ok = all(out.get(k) == v for k, v in partial.items())
            results.append(
                {
                    "case_id": case_id,
                    "ok": bool(q["passed"] and partial_ok),
                    "quality": q,
                    "error": "",
                }
            )
        except Exception as exc:  # noqa: BLE001
            results.append({"case_id": case_id, "ok": False, "quality": {}, "error": str(exc)})

    baseline = payload.get("baseline_logic")
    regression = list(payload.get("regression_cases") or [])
    reg_results: list[dict[str, Any]] = []
    if baseline and regression:
        for rc in regression:
            rid = str(rc.get("case_id") or "regression")
            inp = dict(rc.get("input_data") or {})
            try:
                execute_static_logic(baseline, inp)
                reg_results.append({"case_id": rid, "ok": True, "error": ""})
            except Exception as exc:  # noqa: BLE001
                reg_results.append({"case_id": rid, "ok": False, "error": str(exc)})

    passed = all(r.get("ok") for r in results) and all(r.get("ok") for r in reg_results)
    issues = [f"{r['case_id']}:{r.get('error') or r.get('quality')}" for r in results if not r.get("ok")]
    issues += [f"reg:{r['case_id']}:{r.get('error')}" for r in reg_results if not r.get("ok")]
    return {
        "passed": passed,
        "cases_run": len(results),
        "cases_passed": sum(1 for r in results if r.get("ok")),
        "issues": issues,
        "case_results": results,
        "regression_results": reg_results,
    }


def _process_target(q: Queue, payload: dict[str, Any]) -> None:
    try:
        q.put(_sandbox_worker_payload(payload))
    except Exception as exc:  # noqa: BLE001
        q.put(
            {
                "passed": False,
                "cases_run": 0,
                "cases_passed": 0,
                "issues": [f"sandbox_worker_crash:{exc}"],
                "case_results": [],
                "regression_results": [],
            }
        )


@dataclass(slots=True)
class SandboxResult:
    passed: bool
    cases_run: int
    cases_passed: int
    issues: list[str] = field(default_factory=list)
    subprocess_used: bool = True
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "cases_run": self.cases_run,
            "cases_passed": self.cases_passed,
            "issues": list(self.issues),
            "subprocess_used": self.subprocess_used,
            "raw": dict(self.raw),
        }


class SandboxRunner:
    """Run validation cases inside a spawned subprocess."""

    def __init__(self, *, timeout_seconds: float = 20.0):
        self.timeout_seconds = timeout_seconds

    def validate(
        self,
        *,
        logic: dict[str, Any],
        gate: dict[str, Any],
        cases: list[dict[str, Any]],
        baseline_logic: dict[str, Any] | None = None,
        regression_cases: list[dict[str, Any]] | None = None,
    ) -> SandboxResult:
        if not cases and not regression_cases:
            payload = {
                "logic": logic,
                "gate": gate,
                "cases": [],
                "baseline_logic": baseline_logic,
                "regression_cases": regression_cases or [],
            }
            raw = _sandbox_worker_payload(payload)
            return SandboxResult(
                passed=raw["passed"],
                cases_run=raw["cases_run"],
                cases_passed=raw["cases_passed"],
                issues=list(raw["issues"]),
                subprocess_used=False,
                raw=raw,
            )

        ctx = get_context("spawn")
        q: Queue = ctx.Queue()
        payload: dict[str, Any] = {
            "logic": logic,
            "gate": gate,
            "cases": cases,
            "baseline_logic": baseline_logic,
            "regression_cases": regression_cases or [],
        }
        proc = ctx.Process(target=_process_target, args=(q, payload))
        proc.start()
        proc.join(timeout=self.timeout_seconds)
        if proc.is_alive():
            proc.terminate()
            proc.join(timeout=2)
            return SandboxResult(
                passed=False,
                cases_run=0,
                cases_passed=0,
                issues=[f"sandbox_timeout>{self.timeout_seconds}s"],
                subprocess_used=True,
                raw={},
            )
        try:
            raw = q.get_nowait()
        except Exception:  # noqa: BLE001
            raw = {
                "passed": False,
                "cases_run": 0,
                "cases_passed": 0,
                "issues": ["sandbox_empty_queue"],
            }
        return SandboxResult(
            passed=bool(raw.get("passed")),
            cases_run=int(raw.get("cases_run") or 0),
            cases_passed=int(raw.get("cases_passed") or 0),
            issues=list(raw.get("issues") or []),
            subprocess_used=True,
            raw=raw,
        )
