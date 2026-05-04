from __future__ import annotations

from pathlib import Path

from eskill import (
    DualLayerBridge,
    ESkill,
    ESkillRuntime,
    EmployeeLayerConfig,
    FaultClassifier,
    FaultLayer,
    JsonSkillStore,
    SelfHealingConfig,
    SkillVersion,
    TriggerPolicy,
)


def make_logic_store(path: Path) -> JsonSkillStore:
    store = JsonSkillStore(path)
    store.save_skill(
        ESkill(
            skill_id="logic-writer",
            name="Logic Writer",
            domain="ESkill",
            active_version=1,
            versions=[
                SkillVersion(
                    version=1,
                    static_logic={
                        "type": "template_transform",
                        "template": "Hi ${topic}",
                        "dynamic_template": "Hi ${topic}. ${details}",
                        "required_fields": ["topic"],
                        "output_var": "text",
                        "domain_keywords": ["ESkill"],
                        "healing_hints": {"layer": "logic"},
                        "metadata": {
                            "sandbox_cases": [
                                {
                                    "case_id": "golden",
                                    "input_data": {
                                        "topic": "ESkill",
                                        "details": "logic branch repaired",
                                    },
                                }
                            ]
                        },
                    },
                    trigger_policy=TriggerPolicy(),
                    quality_gate={"min_length": 24},
                )
            ],
        )
    )
    return store


def test_fault_classifier_architecture_timeout() -> None:
    diagnosis = FaultClassifier.classify(
        trigger_reason="error",
        error=TimeoutError("dependency timed out"),
        quality_report=None,
        static_logic={},
        input_data={},
    )

    assert diagnosis.layer == FaultLayer.ARCHITECTURE
    assert diagnosis.to_dict()["fault_type"] == "timeout"


def test_logic_healing_uses_sandbox_and_canary_rollout(tmp_path: Path) -> None:
    store = make_logic_store(tmp_path / "registry.json")
    runtime = ESkillRuntime(
        store,
        healing=SelfHealingConfig(
            enabled=True,
            rollout_mode="canary",
            canary_percent=10,
            sandbox_timeout_seconds=10,
        ),
    )

    run = runtime.run(
        "logic-writer",
        {"topic": "ESkill", "details": "logic branch repaired"},
    )

    skill = store.get_skill("logic-writer")
    assert run.stage == "solidified"
    assert run.diagnosis["layer"] == "logic"
    assert run.sandbox_summary["passed"] is True
    assert skill.active_version == 1
    assert skill.rollout["phase"] == "canary"
    assert skill.rollout["candidate_version"] == 2

    events = store.list_events("logic-writer")
    assert any(e["event_type"] == "fault_diagnosed" for e in events)
    assert any(e["event_type"] == "sandbox_validation" for e in events)

    candidate = runtime.run(
        "logic-writer",
        {
            "topic": "ESkill",
            "details": "logic branch repaired",
            "_eskill": {"force_candidate": True},
        },
        quality_gate={"min_length": 1},
    )
    assert candidate.stage == "static"
    assert candidate.output_data["text"] == "Hi ESkill. logic branch repaired"


def test_dual_layer_bridge_receives_self_healing_signals(tmp_path: Path) -> None:
    store = make_logic_store(tmp_path / "registry.json")
    bridge = DualLayerBridge(store)
    bridge.create_employee("emp-1", EmployeeLayerConfig())
    runtime = ESkillRuntime(
        store,
        healing=SelfHealingConfig(enabled=True, rollout_mode="shadow", sandbox_timeout_seconds=10),
    )
    bridge.attach_runtime_healing(runtime, "logic-writer")

    runtime.run("logic-writer", {"topic": "ESkill", "details": "logic branch repaired"})

    report = bridge.get_dual_layer_report()
    emp_stats = report["employee_layer"]["employee_stats"]["emp-1"]
    signals = emp_stats["self_healing_signals"]
    assert any(s["signal"] == "fault_diagnosed" for s in signals)
    assert any(s["signal"] == "sandbox_validation" for s in signals)
    assert bridge.get_recent_self_healing()
