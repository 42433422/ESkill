"""Brief-first vs direct generation comparison.

Brief-first asks the LLM to lay out a spec (signature + at least 3 test cases)
*before* writing code. The factory then forces the LLM to honor that spec on
the second call. This typically catches issues the LLM would have missed in a
single pass and dramatically improves first-shot quality.

The mock here just records the call sequence so you can see brief_first
issues two prompts and direct issues one.
"""

from __future__ import annotations

import json
import tempfile

from eskill.vibe_coding import MockLLM, VibeCoder


def _spec_response() -> str:
    return json.dumps(
        {
            "skill_id": "sum-list",
            "name": "Sum list",
            "domain": "",
            "function_name": "sum_list",
            "purpose": "把数字列表求和",
            "signature": {"params": ["values"], "return_type": "dict", "required_params": ["values"]},
            "dependencies": [],
            "test_cases": [
                {"case_id": "happy", "input_data": {"values": [1, 2, 3]}, "expected_output": {"total": 6}},
                {"case_id": "empty", "input_data": {"values": []}, "expected_output": {"total": 0}},
                {"case_id": "negatives", "input_data": {"values": [-1, -2, 3]}, "expected_output": {"total": 0}},
            ],
            "quality_gate": {"required_keys": ["total"]},
            "domain_keywords": [],
        }
    )


def _code_response() -> str:
    return json.dumps(
        {
            "source_code": (
                "def sum_list(values):\n"
                "    if not isinstance(values, list):\n"
                "        return {'total': 0, 'error': 'values_not_list'}\n"
                "    total = 0\n"
                "    for x in values:\n"
                "        if isinstance(x, (int, float)):\n"
                "            total += x\n"
                "    return {'total': total}\n"
            )
        }
    )


def run_brief_first(tmp: str) -> None:
    print("== brief_first ==")
    llm = MockLLM([_spec_response(), _code_response()])
    coder = VibeCoder(llm=llm, store_dir=tmp + "/bf", llm_for_repair=False)
    skill = coder.code("写一个函数把数字列表求和", mode="brief_first")
    print(f"  LLM rounds: {len(llm.calls)}")
    for idx, call in enumerate(llm.calls, start=1):
        kind = "spec (no code yet)" if "先不要写代码" in call.system else "code (follow spec)"
        print(f"    round {idx}: {kind}")
    print(f"  v1 source_code:\n    {skill.get_active_version().source_code.splitlines()[0]} ...")


def run_direct(tmp: str) -> None:
    print("\n== direct ==")
    payload = json.loads(_spec_response())
    payload["source_code"] = json.loads(_code_response())["source_code"]
    llm = MockLLM([json.dumps(payload)])
    coder = VibeCoder(llm=llm, store_dir=tmp + "/d", llm_for_repair=False)
    skill = coder.code("写一个函数把数字列表求和", mode="direct")
    print(f"  LLM rounds: {len(llm.calls)}")
    print(f"  v1 source_code:\n    {skill.get_active_version().source_code.splitlines()[0]} ...")


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        run_brief_first(tmp)
        run_direct(tmp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
