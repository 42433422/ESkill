"""Test the LLM client abstraction and MockLLM behavior."""

from __future__ import annotations

import json

import pytest

from eskill.vibe_coding.nl.llm import LLMError, MockLLM


def test_mock_llm_queue_returns_in_order():
    llm = MockLLM([json.dumps({"a": 1}), json.dumps({"b": 2}), json.dumps({"c": 3})])
    assert json.loads(llm.chat("sys", "u1"))["a"] == 1
    assert json.loads(llm.chat("sys", "u2"))["b"] == 2
    assert json.loads(llm.chat("sys", "u3"))["c"] == 3


def test_mock_llm_queue_repeats_last_after_exhaustion():
    llm = MockLLM([json.dumps({"a": 1}), json.dumps({"b": 2})])
    llm.chat("sys", "u1")
    llm.chat("sys", "u2")
    # Third call should repeat the last response
    assert json.loads(llm.chat("sys", "u3"))["b"] == 2


def test_mock_llm_records_calls():
    llm = MockLLM([json.dumps({"x": 1})])
    llm.chat("system here", "user here", json_mode=True)
    assert len(llm.calls) == 1
    assert llm.calls[0].system == "system here"
    assert llm.calls[0].user == "user here"


def test_mock_llm_map_matches_substring():
    llm = MockLLM(
        {
            "weather": json.dumps({"kind": "weather"}),
            "default": json.dumps({"kind": "default"}),
        }
    )
    assert json.loads(llm.chat("sys", "I want weather data"))["kind"] == "weather"
    assert json.loads(llm.chat("sys", "something else"))["kind"] == "default"


def test_mock_llm_raises_on_bad_json_when_json_mode():
    llm = MockLLM(["not json at all"])
    with pytest.raises(LLMError):
        llm.chat("sys", "u", json_mode=True)


def test_mock_llm_passes_through_when_json_mode_false():
    llm = MockLLM(["plain text"])
    assert llm.chat("sys", "u", json_mode=False) == "plain text"


def test_mock_llm_empty_queue_raises():
    llm = MockLLM([])
    with pytest.raises(LLMError):
        llm.chat("sys", "u")


def test_mock_llm_map_no_match_raises():
    llm = MockLLM({"weather": json.dumps({"k": "v"})})  # No default
    with pytest.raises(LLMError):
        llm.chat("sys", "anything")
