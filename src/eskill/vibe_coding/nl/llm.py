"""LLM client abstraction for vibe coding.

We support three implementations out of the box:

- ``MockLLM`` — deterministic responses for tests / offline demos. No external
  dependency. Selects a response by call order or by keyword match against the
  user prompt.
- ``OpenAILLM`` — thin wrapper over the official ``openai`` SDK. Optional;
  install with ``pip install openai``.
- ``LLMClient`` — the protocol any user-supplied client must satisfy.

The contract is intentionally small: ``chat(system, user, *, json_mode=True)``
returns a string. The factories that consume an ``LLMClient`` are responsible
for parsing JSON / handling retries.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Protocol, Sequence


class LLMError(RuntimeError):
    """Raised when the LLM returns an empty / unusable response."""


class LLMClient(Protocol):
    """Minimum contract every vibe-coding LLM client must satisfy."""

    def chat(
        self,
        system: str,
        user: str,
        *,
        json_mode: bool = True,
    ) -> str: ...


@dataclass(slots=True)
class _CallRecord:
    system: str
    user: str
    response: str


class MockLLM:
    """Deterministic LLM stand-in used by tests and offline demos.

    Two construction modes:

    1. ``MockLLM([resp1, resp2, ...])`` — responses are returned in call order.
       After the queue is exhausted the last response is repeated; if the queue
       was empty an :class:`LLMError` is raised.
    2. ``MockLLM({"weather": resp1, "default": resp2})`` — match by substring
       in the user prompt; ``default`` is used when no key matches.

    The ``calls`` attribute records all interactions for assertions in tests.
    """

    def __init__(self, responses: Sequence[str] | dict[str, str]):
        self._mode: str
        self._queue: list[str] = []
        self._map: dict[str, str] = {}
        if isinstance(responses, dict):
            self._mode = "map"
            self._map = {str(k): str(v) for k, v in responses.items()}
        else:
            self._mode = "queue"
            self._queue = [str(r) for r in responses]
        self.calls: list[_CallRecord] = []

    def chat(self, system: str, user: str, *, json_mode: bool = True) -> str:
        response = self._pick(user)
        self.calls.append(_CallRecord(system=system, user=user, response=response))
        if json_mode:
            try:
                json.loads(response)
            except (TypeError, ValueError) as exc:
                raise LLMError(
                    f"MockLLM response is not valid JSON for user prompt "
                    f"{user[:80]!r}: {exc}"
                ) from exc
        return response

    def _pick(self, user: str) -> str:
        if self._mode == "queue":
            if not self._queue:
                if self.calls:
                    return self.calls[-1].response
                raise LLMError("MockLLM has no responses configured")
            head = self._queue[0]
            if len(self._queue) > 1:
                self._queue.pop(0)
            return head
        for key, value in self._map.items():
            if key == "default":
                continue
            if key.lower() in user.lower():
                return value
        if "default" in self._map:
            return self._map["default"]
        raise LLMError(
            f"MockLLM has no response keyed for prompt {user[:80]!r}"
        )

    def remaining(self) -> int:
        if self._mode == "queue":
            return max(0, len(self._queue) - 1)
        return len(self._map)

    def history(self) -> Iterable[_CallRecord]:
        return tuple(self.calls)


class OpenAILLM:
    """Thin wrapper around the official ``openai`` SDK.

    Mirrors the call style used in :mod:`eskill.llm_skill_author.OpenAISkillAuthor`
    so platform LLM credentials and base URLs work identically.
    """

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o-mini",
        *,
        base_url: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.temperature = float(temperature)
        self.max_tokens = max_tokens

    def chat(self, system: str, user: str, *, json_mode: bool = True) -> str:
        try:
            from openai import OpenAI  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ImportError(
                "openai package is required for OpenAILLM. "
                "Install with: pip install openai"
            ) from exc

        client_kwargs: dict[str, Any] = {"api_key": self.api_key}
        if self.base_url:
            client_kwargs["base_url"] = self.base_url
        client = OpenAI(**client_kwargs)

        request_kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.temperature,
        }
        if self.max_tokens is not None:
            request_kwargs["max_tokens"] = int(self.max_tokens)
        if json_mode:
            request_kwargs["response_format"] = {"type": "json_object"}

        response = client.chat.completions.create(**request_kwargs)
        content = (response.choices[0].message.content or "").strip()
        if not content:
            raise LLMError("OpenAI returned empty content")
        return content


@dataclass(slots=True)
class LLMRouter:
    """Optional helper for tests / multi-stage flows.

    Wrap an ``LLMClient`` and replace it transparently mid-flow without
    rebuilding the factory tree (useful for the brief-first → code two-step
    when tests want different mock queues per stage).
    """

    client: LLMClient
    overrides: dict[str, LLMClient] = field(default_factory=dict)

    def chat(self, system: str, user: str, *, json_mode: bool = True, stage: str | None = None) -> str:
        target = self.overrides.get(stage or "", self.client)
        return target.chat(system, user, json_mode=json_mode)
