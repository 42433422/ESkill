"""Test NLConfigSkillFactory: NL → ESkill (config layer)."""

from __future__ import annotations

import json

from eskill import ESkillRuntime, JsonSkillStore
from eskill.vibe_coding import MockLLM
from eskill.vibe_coding.config_factory import NLConfigSkillFactory


def _payload(skill_id: str = "greet") -> dict:
    return {
        "skill_id": skill_id,
        "name": "Greet",
        "domain": "问候",
        "static_logic": {
            "type": "template_transform",
            "template": "你好 ${name}",
            "required_fields": ["name"],
            "output_var": "greeting",
            "domain_keywords": ["你好"],
        },
        "quality_gate": {"required_keys": ["greeting"]},
        "trigger_policy": {"on_error": True, "on_quality_below_threshold": True},
    }


def test_generate_persists_config_skill(tmp_path):
    store = JsonSkillStore(tmp_path / "config.json")
    llm = MockLLM([json.dumps(_payload("greet"))])
    factory = NLConfigSkillFactory(llm, store)
    skill = factory.generate("帮我写个问候技能")
    assert skill.skill_id == "greet"
    assert skill.active_version == 1
    assert store.has_skill("greet")


def test_generate_uses_skill_id_override(tmp_path):
    store = JsonSkillStore(tmp_path / "config.json")
    payload = _payload("auto-id")
    payload.pop("skill_id", None)
    llm = MockLLM([json.dumps(payload)])
    factory = NLConfigSkillFactory(llm, store)
    skill = factory.generate("brief", skill_id="user_id")
    assert skill.skill_id == "user-id"


def test_generated_skill_runs_via_eskill_runtime(tmp_path):
    store = JsonSkillStore(tmp_path / "config.json")
    llm = MockLLM([json.dumps(_payload("greet"))])
    factory = NLConfigSkillFactory(llm, store)
    factory.generate("brief")

    runtime = ESkillRuntime(store)
    result = runtime.run("greet", {"name": "Ada"})
    assert result.stage == "static"
    assert "greeting" in result.output_data


def test_generate_blueprint_does_not_persist(tmp_path):
    store = JsonSkillStore(tmp_path / "config.json")
    llm = MockLLM([json.dumps(_payload("ephemeral"))])
    factory = NLConfigSkillFactory(llm, store)
    bp = factory.generate_blueprint("brief")
    assert bp.skill_id == "ephemeral"
    assert not store.has_skill("ephemeral")
