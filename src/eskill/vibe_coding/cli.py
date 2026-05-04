"""``python -m eskill.vibe_coding`` command-line entry point.

Subcommands::

    code "..."        — generate a CodeSkill from a brief
    workflow "..."    — generate a complete workflow + all its skills
    run <skill_id> '<json>'
                      — execute a code skill (failure auto-repair on)
    rollback <skill_id> <version>
                      — pin a different active version
    report [skill_id] — patch / evolution stats
    history <skill_id>
                      — chronological patch list
    list              — list code + config skills

LLM defaults to OpenAI via ``OPENAI_API_KEY``; pass ``--mock`` to use a
MockLLM that just echoes a canned response (handy for smoke tests).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from .facade import VibeCoder
from .nl.llm import MockLLM, OpenAILLM


def _build_llm(args: argparse.Namespace) -> Any:
    if args.mock:
        return MockLLM(
            {
                "default": json.dumps(
                    {
                        "skill_id": "demo",
                        "name": "demo",
                        "domain": "demo",
                        "function_name": "demo",
                        "source_code": "def demo(value=''):\n    return {'echo': str(value)}",
                        "signature": {
                            "params": ["value"],
                            "return_type": "dict",
                            "required_params": [],
                        },
                        "dependencies": [],
                        "test_cases": [
                            {
                                "case_id": "happy",
                                "input_data": {"value": "ping"},
                                "expected_output": {"echo": "ping"},
                            }
                        ],
                        "quality_gate": {"required_keys": ["echo"]},
                        "domain_keywords": ["demo"],
                    }
                )
            }
        )
    api_key = args.api_key or os.environ.get("OPENAI_API_KEY") or ""
    if not api_key:
        print("error: --api-key or OPENAI_API_KEY required (use --mock for offline)", file=sys.stderr)
        raise SystemExit(2)
    return OpenAILLM(api_key=api_key, model=args.model, base_url=args.base_url)


def _make_coder(args: argparse.Namespace) -> VibeCoder:
    llm = _build_llm(args)
    return VibeCoder(llm=llm, store_dir=args.store_dir, llm_for_repair=not args.mock)


def _cmd_code(args: argparse.Namespace) -> int:
    coder = _make_coder(args)
    skill = coder.code(args.brief, mode=args.mode, skill_id=args.skill_id)
    print(json.dumps(skill.to_dict(), ensure_ascii=False, indent=2))
    return 0


def _cmd_workflow(args: argparse.Namespace) -> int:
    coder = _make_coder(args)
    report = coder.workflow_with_report(args.brief)
    print(
        json.dumps(
            {
                "workflow_id": report.workflow_id,
                "graph": report.graph.to_dict(),
                "code_skills_created": report.code_skills_created,
                "config_skills_created": report.config_skills_created,
                "warnings": report.warnings,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    coder = _make_coder(args)
    try:
        input_data = json.loads(args.input_json)
    except json.JSONDecodeError as exc:
        print(f"error: invalid JSON input: {exc}", file=sys.stderr)
        return 2
    if not isinstance(input_data, dict):
        print("error: input must be a JSON object", file=sys.stderr)
        return 2
    run = coder.run(args.skill_id, input_data)
    print(json.dumps(run.to_dict(), ensure_ascii=False, indent=2))
    return 0


def _cmd_rollback(args: argparse.Namespace) -> int:
    coder = _make_coder(args)
    skill = coder.rollback(args.skill_id, args.version)
    print(
        json.dumps(
            {
                "skill_id": getattr(skill, "skill_id", ""),
                "active_version": getattr(skill, "active_version", 0),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    coder = _make_coder(args)
    report = coder.report(args.skill_id)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def _cmd_history(args: argparse.Namespace) -> int:
    coder = _make_coder(args)
    history = coder.history(args.skill_id)
    print(json.dumps([r.to_dict() for r in history], ensure_ascii=False, indent=2))
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    coder = _make_coder(args)
    out = {
        "code_skills": [s.skill_id for s in coder.list_code_skills()],
        "config_skills": [s.skill_id for s in coder.list_config_skills()],
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m eskill.vibe_coding",
        description="Vibe Coding for ESkill — NL to self-healing skills",
    )
    parser.add_argument("--store-dir", default="./vibe_coding_data", help="store directory")
    parser.add_argument("--api-key", default="", help="OpenAI API key (defaults to env)")
    parser.add_argument("--base-url", default=None, help="optional OpenAI base URL")
    parser.add_argument("--model", default="gpt-4o-mini", help="OpenAI model name")
    parser.add_argument("--mock", action="store_true", help="use MockLLM (offline)")

    sub = parser.add_subparsers(dest="cmd", required=True)

    code = sub.add_parser("code", help="generate a CodeSkill")
    code.add_argument("brief")
    code.add_argument("--mode", choices=["direct", "brief_first"], default="brief_first")
    code.add_argument("--skill-id", default=None)
    code.set_defaults(func=_cmd_code)

    wf = sub.add_parser("workflow", help="generate a complete workflow")
    wf.add_argument("brief")
    wf.set_defaults(func=_cmd_workflow)

    run = sub.add_parser("run", help="execute a code skill")
    run.add_argument("skill_id")
    run.add_argument("input_json", help='JSON object, e.g. \'{"value":"x"}\'')
    run.set_defaults(func=_cmd_run)

    rb = sub.add_parser("rollback", help="rollback a skill to a previous version")
    rb.add_argument("skill_id")
    rb.add_argument("version", type=int)
    rb.set_defaults(func=_cmd_rollback)

    rep = sub.add_parser("report", help="patch / evolution stats")
    rep.add_argument("skill_id", nargs="?", default=None)
    rep.set_defaults(func=_cmd_report)

    hist = sub.add_parser("history", help="chronological patch list")
    hist.add_argument("skill_id")
    hist.set_defaults(func=_cmd_history)

    ls = sub.add_parser("list", help="list known skills")
    ls.set_defaults(func=_cmd_list)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
