"""Natural-language layer of vibe coding: prompts and LLM client."""

from __future__ import annotations

from .llm import LLMClient, LLMError, MockLLM, OpenAILLM
from .prompts import (
    BRIEF_FIRST_CODE_PROMPT,
    BRIEF_FIRST_SPEC_PROMPT,
    CODE_DIRECT_PROMPT,
    CODE_REPAIR_PROMPT,
    WORKFLOW_PROMPT,
)

__all__ = [
    "BRIEF_FIRST_CODE_PROMPT",
    "BRIEF_FIRST_SPEC_PROMPT",
    "CODE_DIRECT_PROMPT",
    "CODE_REPAIR_PROMPT",
    "LLMClient",
    "LLMError",
    "MockLLM",
    "OpenAILLM",
    "WORKFLOW_PROMPT",
]
