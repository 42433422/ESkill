"""Test NLCodeSkillFactory: NL → CodeSkill with sandbox verification + repair."""

from __future__ import annotations

import json

import pytest

from eskill import CodeSkillRuntime, JsonCodeSkillStore
from eskill.vibe_coding import MockLLM
from eskill.vibe_coding.code_factory import NLCodeSkillFactory, VibeCodingError


def _spec(skill_id: str, fn: str = "demo") -> str:
    return json.dumps(
        {
            "skill_id": skill_id,
            "name": skill_id,
            "domain": "",
            "function_name": fn,
            "purpose": "demo",
            "signature": {"params": ["value"], "return_type": "dict", "required_params": ["value"]},
            "dependencies": [],
            "test_cases": [
                {
                    "case_id": "happy",
                    "input_data": {"value": "hi"},
                    "expected_output": {"out": "hi"},
                }
            ],
            "quality_gate": {"required_keys": ["out"]},
            "domain_keywords": [],
        }
    )


def _good_code(fn: str = "demo") -> str:
    return json.dumps(
        {
            "source_code": (
                f"def {fn}(value):\n"
                "    if not isinstance(value, str):\n"
                "        return {'out': '', 'error': 'not_str'}\n"
                "    return {'out': value}\n"
            )
        }
    )


def _bad_code(fn: str = "demo") -> str:
    """Triggers KeyError because subscript access on missing key."""
    return json.dumps(
        {
            "source_code": (
                f"def {fn}(value):\n"
                "    return {'out': value['nope']}\n"
            )
        }
    )


def test_brief_first_happy_path(tmp_path):
    store = JsonCodeSkillStore(tmp_path / "store.json")
    llm = MockLLM([_spec("s1"), _good_code()])
    factory = NLCodeSkillFactory(llm, store)
    skill = factory.generate("any brief")
    assert skill.skill_id == "s1"
    assert skill.active_version == 1
    assert "def demo(value)" in skill.get_active_version().source_code
    # Persisted
    assert store.has_code_skill("s1")


def test_direct_mode_uses_one_call(tmp_path):
    store = JsonCodeSkillStore(tmp_path / "store.json")
    payload = json.loads(_spec("s2"))
    payload["source_code"] = json.loads(_good_code())["source_code"]
    llm = MockLLM([json.dumps(payload)])
    factory = NLCodeSkillFactory(llm, store)
    skill = factory.generate("brief", mode="direct")
    assert skill.skill_id == "s2"
    assert len(llm.calls) == 1


def test_repair_loop_recovers_from_bad_code(tmp_path):
    store = JsonCodeSkillStore(tmp_path / "store.json")
    # spec, then bad code (sandbox-fail), then a repair that returns good code
    llm = MockLLM([_spec("s3"), _bad_code(), _good_code()])
    factory = NLCodeSkillFactory(llm, store, max_repair_rounds=2)
    skill = factory.generate("brief")
    assert skill.skill_id == "s3"
    # 3 LLM calls: spec, code, repair
    assert len(llm.calls) == 3


def test_repair_exhausts_and_raises(tmp_path):
    store = JsonCodeSkillStore(tmp_path / "store.json")
    # Always bad: spec + code(bad) + repair(bad) + repair(bad)
    llm = MockLLM([_spec("s4"), _bad_code(), _bad_code(), _bad_code()])
    factory = NLCodeSkillFactory(llm, store, max_repair_rounds=2)
    with pytest.raises(VibeCodingError):
        factory.generate("brief")


def test_validation_rejects_forbidden_import(tmp_path):
    store = JsonCodeSkillStore(tmp_path / "store.json")
    spec = json.loads(_spec("s5"))
    bad_code = {
        "source_code": (
            "import os\n"
            "def demo(value):\n"
            "    return {'out': value}\n"
        )
    }
    # spec, bad import, repair good
    llm = MockLLM([json.dumps(spec), json.dumps(bad_code), _good_code()])
    factory = NLCodeSkillFactory(llm, store, max_repair_rounds=2)
    skill = factory.generate("brief")
    assert "import os" not in skill.get_active_version().source_code


def test_skill_id_override_takes_effect(tmp_path):
    store = JsonCodeSkillStore(tmp_path / "store.json")
    llm = MockLLM([_spec("auto-id"), _good_code()])
    factory = NLCodeSkillFactory(llm, store)
    skill = factory.generate("brief", skill_id="user_chose_this_id")
    assert skill.skill_id == "user-chose-this-id"


def test_runtime_self_heals_after_persisted(tmp_path):
    """Generated skill is immediately runnable; runtime auto-patches a KeyError."""
    store = JsonCodeSkillStore(tmp_path / "store.json")
    spec = {
        "skill_id": "extract",
        "name": "extract",
        "domain": "",
        "function_name": "extract",
        "purpose": "extract name",
        "signature": {"params": ["user"], "return_type": "dict", "required_params": ["user"]},
        "dependencies": [],
        "test_cases": [
            {
                "case_id": "happy",
                "input_data": {"user": {"name": "ada"}},
                "expected_output": {"name": "ada"},
            }
        ],
        "quality_gate": {"required_keys": ["name"]},
        "domain_keywords": [],
    }
    code = {"source_code": "def extract(user):\n    return {'name': user['name']}\n"}
    llm = MockLLM([json.dumps(spec), json.dumps(code)])
    factory = NLCodeSkillFactory(llm, store)
    skill = factory.generate("extract name from user")
    assert skill.active_version == 1

    runtime = CodeSkillRuntime(store)
    run = runtime.run(skill.skill_id, {"user": {}})
    assert run.stage == "solidified"
    refreshed = store.get_code_skill(skill.skill_id)
    assert refreshed.active_version == 2


def test_repair_method_on_existing_skill(tmp_path):
    store = JsonCodeSkillStore(tmp_path / "store.json")
    llm = MockLLM([_spec("s6"), _good_code()])
    factory = NLCodeSkillFactory(llm, store)
    skill = factory.generate("brief")
    assert skill.active_version == 1

    # Ask the factory to repair: feed a fresh repair response
    factory.llm = MockLLM([_good_code()])
    repaired = factory.repair("s6", failure="user reported edge case x")
    assert repaired.active_version == 2
