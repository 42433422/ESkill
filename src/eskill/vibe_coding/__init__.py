"""Vibe Coding for ESkill — natural-language to executable, self-healing skills.

This subpackage augments the eskill prototype with a vibe-coding layer:

- ``NLCodeSkillFactory`` — turn a natural-language brief into a sandbox-validated
  ``CodeSkill`` and register it into a ``JsonCodeSkillStore``. The generated
  skill is automatically wrapped by ``CodeSkillRuntime`` so failures at runtime
  trigger automatic patch-and-solidify cycles.
- ``NLConfigSkillFactory`` — turn a natural-language brief into a config-layer
  ``ESkill`` (template_transform / employee_task / pipeline) wrapped by
  ``ESkillRuntime`` for self-healing.
- ``NLWorkflowFactory`` — true "AI-generated workflow": a single brief is
  expanded into a multi-node graph where each code-layer node has been
  generated and sandbox-verified end-to-end before the graph is returned.
- ``VibeWorkflowEngine`` — orchestrates the resulting graph, dispatching each
  node through the appropriate self-healing runtime.
- ``PatchLedger`` — patch history, evolution chain, one-click rollback.
- ``VibeCoder`` — the one-stop facade that ties it all together.

A standalone copy of this subpackage lives at ``E:/成都修茈科技有限公司/vibe-coding``
for reuse in other projects; see ``docs/VIBE_CODING.md`` for the relationship
between the two trees.
"""

from __future__ import annotations

from .audit import PatchLedger, PatchRecord
from .code_factory import NLCodeSkillFactory, VibeCodingError
from .config_factory import NLConfigSkillFactory
from .facade import VibeCoder
from .nl.llm import LLMClient, MockLLM, OpenAILLM
from .nl.prompts import (
    BRIEF_FIRST_CODE_PROMPT,
    BRIEF_FIRST_SPEC_PROMPT,
    CODE_DIRECT_PROMPT,
    WORKFLOW_PROMPT,
)
from .workflow_engine import VibeWorkflowEngine, WorkflowRunResult
from .workflow_factory import NLWorkflowFactory
from .workflow_models import VibeWorkflowEdge, VibeWorkflowGraph, VibeWorkflowNode

__all__ = [
    "BRIEF_FIRST_CODE_PROMPT",
    "BRIEF_FIRST_SPEC_PROMPT",
    "CODE_DIRECT_PROMPT",
    "LLMClient",
    "MockLLM",
    "NLCodeSkillFactory",
    "NLConfigSkillFactory",
    "NLWorkflowFactory",
    "OpenAILLM",
    "PatchLedger",
    "PatchRecord",
    "VibeCoder",
    "VibeCodingError",
    "VibeWorkflowEdge",
    "VibeWorkflowEngine",
    "VibeWorkflowGraph",
    "VibeWorkflowNode",
    "WORKFLOW_PROMPT",
    "WorkflowRunResult",
]
