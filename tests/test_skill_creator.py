from __future__ import annotations

import pytest

from eskill import ESkillRuntime, JsonSkillStore, SkillBlueprint, SkillCreator
from eskill.llm_skill_author import blueprint_from_llm_payload
from eskill.skill_creator import normalize_skill_id, validate_static_logic


def test_normalize_skill_id() -> None:
    assert normalize_skill_id("My Cool Skill!") == "my-cool-skill"
    assert normalize_skill_id("  Writer  ") == "writer"


def test_validate_static_logic_pipeline_tool_allowlist() -> None:
    with pytest.raises(ValueError, match="allowlist"):
        validate_static_logic(
            {
                "type": "pipeline",
                "steps": [{"type": "tool_call", "tool": "rm", "output_var": "x"}],
            }
        )


def test_skill_creator_create_and_run(tmp_path) -> None:
    store = JsonSkillStore(tmp_path / "r.json")
    bp = SkillBlueprint.template_transform(
        skill_id="hello",
        name="Hello",
        domain="greeting",
        template="Hello ${name}",
        required_fields=["name"],
        output_var="greeting",
        quality_gate={"min_length": 1},
    )
    skill = SkillCreator.create(store, bp)
    assert skill.skill_id == "hello"
    assert store.has_skill("hello")

    runtime = ESkillRuntime(store)
    run = runtime.run("hello", {"name": "World"}, solidify=False)
    assert run.stage == "static"
    assert run.output_data["greeting"] == "Hello World"


def test_skill_creator_duplicate_raises(tmp_path) -> None:
    store = JsonSkillStore(tmp_path / "r.json")
    bp = SkillBlueprint.template_transform(
        skill_id="dup",
        name="Dup",
        domain="d",
        template="x",
        required_fields=[],
        output_var="r",
    )
    SkillCreator.create(store, bp)
    with pytest.raises(KeyError, match="already exists"):
        SkillCreator.create(store, bp)


def test_skill_creator_overwrite(tmp_path) -> None:
    store = JsonSkillStore(tmp_path / "r.json")
    bp1 = SkillBlueprint.template_transform(
        skill_id="ov",
        name="One",
        domain="d",
        template="A",
        required_fields=[],
        output_var="r",
    )
    SkillCreator.create(store, bp1)
    bp2 = SkillBlueprint.template_transform(
        skill_id="ov",
        name="Two",
        domain="d",
        template="B",
        required_fields=[],
        output_var="r",
    )
    SkillCreator.create(store, bp2, overwrite=True)
    assert store.get_skill("ov").name == "Two"


def test_skill_creator_ensure_idempotent(tmp_path) -> None:
    store = JsonSkillStore(tmp_path / "r.json")
    bp = SkillBlueprint.template_transform(
        skill_id="idempo",
        name="I",
        domain="d",
        template="z",
        required_fields=[],
        output_var="r",
    )
    s1, c1 = SkillCreator.ensure(store, bp)
    s2, c2 = SkillCreator.ensure(store, bp)
    assert c1 is True and c2 is False
    assert s1.skill_id == s2.skill_id


def test_blueprint_from_llm_payload() -> None:
    bp = blueprint_from_llm_payload(
        {
            "skill_id": "demo-skill",
            "name": "Demo",
            "domain": "testing",
            "static_logic": {
                "type": "template_transform",
                "template": "T ${x}",
                "required_fields": ["x"],
                "output_var": "out",
            },
            "quality_gate": {},
            "trigger_policy": {},
        }
    )
    assert bp.skill_id == "demo-skill"
    assert bp.static_logic["template"] == "T ${x}"


def test_json_skill_store_has_skill(tmp_path) -> None:
    store = JsonSkillStore(tmp_path / "r.json")
    assert store.has_skill("none") is False
