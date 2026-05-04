"""Test the vibe_coding CLI subcommands using --mock to stay offline."""

from __future__ import annotations

import json

import pytest

from eskill.vibe_coding.cli import build_parser, main


def test_parser_has_all_subcommands():
    parser = build_parser()
    sub = next(
        a for a in parser._subparsers._group_actions if hasattr(a, "choices")  # type: ignore[attr-defined]
    )
    assert {"code", "workflow", "run", "rollback", "report", "history", "list"}.issubset(
        sub.choices.keys()
    )


def test_cli_code_with_mock(tmp_path, capsys):
    rc = main(
        [
            "--store-dir",
            str(tmp_path),
            "--mock",
            "code",
            "make a demo skill",
            "--mode",
            "direct",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["skill_id"] == "demo"


def test_cli_run_after_code(tmp_path, capsys):
    main(
        [
            "--store-dir",
            str(tmp_path),
            "--mock",
            "code",
            "make demo",
            "--mode",
            "direct",
        ]
    )
    capsys.readouterr()
    rc = main(
        [
            "--store-dir",
            str(tmp_path),
            "--mock",
            "run",
            "demo",
            json.dumps({"value": "ping"}),
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["stage"] == "static"
    assert payload["output_data"]["echo"] == "ping"


def test_cli_list_after_code(tmp_path, capsys):
    main(
        [
            "--store-dir",
            str(tmp_path),
            "--mock",
            "code",
            "make demo",
            "--mode",
            "direct",
        ]
    )
    capsys.readouterr()
    rc = main(["--store-dir", str(tmp_path), "--mock", "list"])
    assert rc == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert "demo" in payload["code_skills"]


def test_cli_run_invalid_json_returns_2(tmp_path, capsys):
    main(
        [
            "--store-dir",
            str(tmp_path),
            "--mock",
            "code",
            "make demo",
            "--mode",
            "direct",
        ]
    )
    capsys.readouterr()
    rc = main(
        [
            "--store-dir",
            str(tmp_path),
            "--mock",
            "run",
            "demo",
            "not json",
        ]
    )
    assert rc == 2


def test_cli_report_after_code(tmp_path, capsys):
    main(
        [
            "--store-dir",
            str(tmp_path),
            "--mock",
            "code",
            "make demo",
            "--mode",
            "direct",
        ]
    )
    capsys.readouterr()
    rc = main(["--store-dir", str(tmp_path), "--mock", "report"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["totals"]["skills"] >= 1
