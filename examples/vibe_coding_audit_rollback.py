"""Audit & rollback demo.

Generate a skill, deliberately trigger a self-heal cycle, then list patch
history and roll back to v1 with a single call.
"""

from __future__ import annotations

import json
import tempfile

from eskill.vibe_coding import MockLLM, VibeCoder


def build_mock_llm() -> MockLLM:
    spec = {
        "skill_id": "extract-name",
        "name": "Extract Name",
        "domain": "",
        "function_name": "extract_name",
        "purpose": "把 user dict 里的 name 字段读出来",
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
    code = {"source_code": "def extract_name(user):\n    return {'name': user['name']}\n"}
    return MockLLM([json.dumps(spec), json.dumps(code)])


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        coder = VibeCoder(llm=build_mock_llm(), store_dir=tmp, llm_for_repair=False)
        skill = coder.code("把 user 里的 name 取出来")
        print("Generated:", skill.skill_id, "v", skill.active_version)

        # Trigger self-healing
        coder.run(skill.skill_id, {"user": {}})

        # Inspect history + chain
        print("\nEvolution chain:")
        for entry in coder.evolution_chain(skill.skill_id):
            print(f"  v{entry['version']}  active={entry['active']}  source_run_id={entry['source_run_id']}")

        print("\nPatch history (most recent last):")
        for record in coder.history(skill.skill_id):
            print(
                f"  stage={record.stage} reason={record.summary or '-'} "
                f"err={(record.error or '-')[:60]}"
            )

        # Rollback to v1
        print("\nRolling back to v1 ...")
        coder.rollback(skill.skill_id, 1)
        post = coder.list_code_skills()[0]
        print("active_version after rollback:", post.active_version)

        # Final report
        print("\nReport:", json.dumps(coder.report(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
