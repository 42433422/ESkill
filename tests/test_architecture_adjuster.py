from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

import pytest

from eskill import (
    ESkill,
    ESkillRuntime,
    JsonSkillStore,
    RuleBasedDynamicAdapter,
    SelfHealingConfig,
    SkillVersion,
    TriggerPolicy,
)
from eskill.architecture import (
    ArchitectureAdjuster,
    ArchitectureExecutor,
    ArchitectureProfile,
)
from eskill.diagnostics import FaultDiagnosis, FaultLayer, FaultType
from eskill.models import DynamicPatch
from eskill.patch_planner import _overlay_architecture_resilience
from eskill.sandbox import SandboxResult, SandboxRunner


def test_adjuster_all_architecture_fault_types() -> None:
    base = ArchitectureProfile(
        timeout_seconds=10.0,
        max_retries=0,
        bulkhead_max_concurrency=32,
    )
    types = [
        (FaultType.TIMEOUT, lambda p: p.timeout_seconds > 10.0 and p.max_retries > 0),
        (FaultType.DEPENDENCY, lambda p: p.fallback_strategy == "echo_input" or p.breaker_failure_threshold < 5),
        (FaultType.CONCURRENCY, lambda p: p.bulkhead_max_concurrency < 32),
        (FaultType.RESOURCE_EXHAUSTION, lambda p: p.bulkhead_max_concurrency < 32),
        (FaultType.DEADLOCK, lambda p: p.max_retries == 1),
    ]
    for ft, check in types:
        d = FaultDiagnosis(layer=FaultLayer.ARCHITECTURE, fault_type=ft, confidence=0.8)
        out = ArchitectureAdjuster.adjust(d, base)
        assert check(out), f"failed for {ft}"


def test_overlay_writes_architecture_profile() -> None:
    logic: dict = {"type": "template_transform", "template": "a", "required_fields": []}
    patch = DynamicPatch(reason="r", changes={"template": "b"})
    d = FaultDiagnosis(
        layer=FaultLayer.ARCHITECTURE,
        fault_type=FaultType.TIMEOUT,
        confidence=0.9,
    )
    merged = _overlay_architecture_resilience(patch, d, logic)
    assert "architecture_profile" in merged.changes
    assert merged.changes["architecture_profile"]["timeout_seconds"] > 10.0


def test_executor_timeout_event() -> None:
    ex = ArchitectureExecutor()
    prof = ArchitectureProfile(timeout_seconds=0.05, max_retries=0, breaker_failure_threshold=99)
    events: list[tuple[str, dict]] = []

    def on_event(et: str, d: dict) -> None:
        events.append((et, d))

    def slow() -> dict:
        time.sleep(1.0)
        return {}

    with pytest.raises(TimeoutError):
        ex.execute("s1", prof, slow, input_data={}, on_event=on_event)
    assert any(et == "architecture_timeout" for et, _ in events)


def test_executor_breaker_and_fallback_echo() -> None:
    ex = ArchitectureExecutor()
    prof = ArchitectureProfile(
        timeout_seconds=5.0,
        max_retries=0,
        breaker_failure_threshold=2,
        fallback_strategy="echo_input",
    )
    events: list[str] = []

    def on_event(et: str, d: dict) -> None:
        events.append(et)

    def boom() -> dict:
        raise ValueError("down")

    with pytest.raises(ValueError):
        ex.execute("sk", prof, boom, input_data={"a": 1}, on_event=on_event)
    r2 = ex.execute("sk", prof, boom, input_data={"a": 1}, on_event=on_event)
    assert r2.get("echo") == {"a": 1}
    assert "architecture_fallback" in events


def test_eskill_runtime_architecture_timeout_emits_event(tmp_path: Path) -> None:
    store = JsonSkillStore(tmp_path / "r.json")
    store.save_skill(
        ESkill(
            skill_id="t-arch",
            name="T",
            domain="test",
            active_version=1,
            versions=[
                SkillVersion(
                    version=1,
                    static_logic={
                        "type": "template_transform",
                        "template": "hello",
                        "required_fields": [],
                        "output_var": "text",
                        "architecture_profile": {"timeout_seconds": 0.05, "max_retries": 0, "breaker_failure_threshold": 99},
                    },
                    trigger_policy=TriggerPolicy(),
                    quality_gate={"min_length": 1},
                )
            ],
        )
    )

    def slow_execute(logic: dict, input_data: dict) -> dict:
        time.sleep(0.5)
        return {"text": "hello", "logic_type": "template_transform"}

    rt = ESkillRuntime(store)
    with patch("eskill.runtime.static_executor.execute_static_logic", side_effect=slow_execute):
        run = rt.run("t-arch", {})
    evs = store.list_events("t-arch")
    assert any(e.get("event_type") == "architecture_timeout" for e in evs)


def test_eskill_architecture_solidifies_profile(tmp_path: Path) -> None:
    """Architecture-layer diagnosis produces architecture_profile on dynamic patch."""
    store = JsonSkillStore(tmp_path / "s.json")
    store.save_skill(
        ESkill(
            skill_id="arch-solid",
            name="A",
            domain="test",
            active_version=1,
            versions=[
                SkillVersion(
                    version=1,
                    static_logic={
                        "type": "template_transform",
                        "template": "short",
                        "dynamic_template": "A" * 55 + " more text about ${topic} to satisfy the quality gate here.",
                        "required_fields": [],
                        "output_var": "text",
                        "domain_keywords": ["test"],
                        "healing_hints": {"layer": "architecture"},
                    },
                    trigger_policy=TriggerPolicy(),
                    quality_gate={"min_length": 50},
                )
            ],
        )
    )

    rt = ESkillRuntime(
        store,
        RuleBasedDynamicAdapter(),
        healing=SelfHealingConfig(enabled=True, sandbox_timeout_seconds=10),
    )
    ok_sb = SandboxResult(passed=True, cases_run=1, cases_passed=1, issues=[])
    with patch.object(SandboxRunner, "validate", return_value=ok_sb):
        run = rt.run("arch-solid", {"topic": "test failure"}, quality_gate={"min_length": 50})
    assert run.stage == "solidified"
    skill = store.get_skill("arch-solid")
    active = skill.get_active_version()
    assert "architecture_profile" in active.static_logic or (
        active.static_logic.get("metadata", {}).get("resilience")
    )
