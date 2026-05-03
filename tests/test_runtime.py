from __future__ import annotations

from pathlib import Path

from eskill import ESkill, ESkillRuntime, JsonSkillStore, SkillVersion, TriggerPolicy


def make_store(path: Path) -> JsonSkillStore:
    store = JsonSkillStore(path)
    store.save_skill(
        ESkill(
            skill_id="writer",
            name="Writer",
            domain="writing",
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
                        "domain_keywords": ["ESkill", "missing"],
                    },
                    trigger_policy=TriggerPolicy(),
                    quality_gate={"min_length": 20},
                )
            ],
        )
    )
    return store


def test_static_run_succeeds_without_solidifying(tmp_path: Path) -> None:
    store = make_store(tmp_path / "registry.json")
    runtime = ESkillRuntime(store)

    run = runtime.run("writer", {"topic": "ESkill"}, quality_gate={"min_length": 1})

    assert run.stage == "static"
    assert run.output_data["text"] == "Hi ESkill"
    assert store.get_skill("writer").active_version == 1


def test_quality_failure_enters_dynamic_and_solidifies(tmp_path: Path) -> None:
    store = make_store(tmp_path / "registry.json")
    runtime = ESkillRuntime(store)

    run = runtime.run("writer", {"topic": "ESkill", "details": "Dynamic adaptation"})

    assert run.stage == "solidified"
    assert run.patch is not None
    assert run.output_data["solidified_version"] == 2
    skill = store.get_skill("writer")
    assert skill.active_version == 2
    assert skill.get_active_version().static_logic["template"] == "Hi ${topic}. ${details}"


def test_static_error_enters_dynamic(tmp_path: Path) -> None:
    store = make_store(tmp_path / "registry.json")
    runtime = ESkillRuntime(store)

    run = runtime.run("writer", {"details": "missing topic but recoverable"})

    assert run.stage == "solidified"
    assert run.patch is not None
    assert "text" in run.output_data


def test_dynamic_rejects_out_of_domain_input(tmp_path: Path) -> None:
    store = make_store(tmp_path / "registry.json")
    runtime = ESkillRuntime(store)

    run = runtime.run(
        "writer",
        {"topic": "unrelated finance", "details": "tax"},
        quality_gate={"min_length": 100},
    )

    assert run.stage == "domain_rejected"
    assert store.get_skill("writer").active_version == 1


def test_dynamic_patch_can_add_pipeline_steps(tmp_path: Path) -> None:
    store = JsonSkillStore(tmp_path / "registry.json")
    store.save_skill(
        ESkill(
            skill_id="pipeline-writer",
            name="Pipeline Writer",
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
                        "allow_steps": True,
                        "domain_keywords": ["ESkill"],
                    },
                    trigger_policy=TriggerPolicy(),
                    quality_gate={"required_keys": ["adaptation_reason"]},
                )
            ],
        )
    )
    runtime = ESkillRuntime(store)

    run = runtime.run("pipeline-writer", {"topic": "ESkill", "details": "pipeline"})

    assert run.stage == "solidified"
    assert run.patch is not None
    assert store.get_skill("pipeline-writer").get_active_version().static_logic["type"] == "pipeline"
