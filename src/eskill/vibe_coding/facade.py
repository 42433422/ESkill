"""VibeCoder — one-stop facade for natural-language → self-healing skills.

This module wires the rest of vibe-coding together so the user only has to
``from eskill.vibe_coding import VibeCoder`` and call a handful of methods:

- ``code(brief)`` — generate a sandbox-validated CodeSkill
- ``config_skill(brief)`` — generate a config-layer ESkill
- ``workflow(brief)`` — true "AI-generated workflow", multi-skill, end-to-end
- ``run(skill_id, input)`` — execute a code skill with self-healing
- ``execute(graph, input)`` — execute a workflow with self-healing per node
- ``history / rollback / report`` — audit trail and one-click rollback

All stores / runtimes are owned by the VibeCoder, so a single ``store_dir``
keeps every artefact (code, config, runs, events) in a predictable place that
can be rsynced or backed up wholesale.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..code import (
    CodeSkill,
    CodeSkillRun,
    CodeSkillRuntime,
    JsonCodeSkillStore,
    OpenAICodePatchGenerator,
    RuleBasedCodePatchGenerator,
)
from ..models import ESkill
from ..runtime import ESkillRuntime
from ..store import JsonSkillStore
from .audit import PatchLedger, PatchRecord
from .code_factory import GenerationMode, NLCodeSkillFactory
from .config_factory import NLConfigSkillFactory
from .nl.llm import LLMClient
from .workflow_engine import VibeWorkflowEngine, WorkflowRunResult
from .workflow_factory import NLWorkflowFactory, WorkflowGenerationReport
from .workflow_models import VibeWorkflowGraph


class VibeCoder:
    """High-level facade. Owns stores + runtimes + factories.

    Construct with at minimum an :class:`LLMClient` (use :class:`MockLLM` for
    offline / test). ``store_dir`` is created on demand; the resulting layout::

        store_dir/
          ├── eskill_store.json    # config-layer skills
          └── code_skill_store.json  # code-layer skills

    Pass ``llm_for_repair=False`` if you want runtime self-healing to rely
    purely on rule-based patching (handy for tests; default uses your LLM).
    """

    def __init__(
        self,
        *,
        llm: LLMClient,
        store_dir: str | Path = "./vibe_coding_data",
        llm_for_repair: bool = True,
        code_runtime: CodeSkillRuntime | None = None,
        config_runtime: ESkillRuntime | None = None,
    ):
        self.llm = llm
        self.store_dir = Path(store_dir)
        self.store_dir.mkdir(parents=True, exist_ok=True)

        self.config_store = JsonSkillStore(self.store_dir / "eskill_store.json")
        self.code_store = JsonCodeSkillStore(self.store_dir / "code_skill_store.json")

        self.code_factory = NLCodeSkillFactory(llm, self.code_store)
        self.config_factory = NLConfigSkillFactory(llm, self.config_store)
        self.workflow_factory = NLWorkflowFactory(
            llm, self.code_factory, self.config_factory
        )

        self.code_runtime = code_runtime or CodeSkillRuntime(
            self.code_store,
            llm_generator=_build_code_patch_generator(llm) if llm_for_repair else None,
        )
        self.config_runtime = config_runtime or ESkillRuntime(self.config_store)
        self.engine = VibeWorkflowEngine(
            code_runtime=self.code_runtime,
            config_runtime=self.config_runtime,
            wrap_with_node_wrapper=False,
        )
        self.ledger = PatchLedger(
            code_store=self.code_store,
            config_store=self.config_store,
        )

    # ------------------------------------------------------------------ generation

    def code(
        self,
        brief: str,
        *,
        mode: GenerationMode = "brief_first",
        skill_id: str | None = None,
        dependencies: list[str] | None = None,
    ) -> CodeSkill:
        """Generate a sandbox-validated CodeSkill from a brief."""
        return self.code_factory.generate(
            brief, mode=mode, skill_id=skill_id, dependencies=dependencies
        )

    def config_skill(self, brief: str, *, skill_id: str | None = None) -> ESkill:
        """Generate a config-layer ESkill from a brief."""
        return self.config_factory.generate(brief, skill_id=skill_id)

    def workflow(self, brief: str) -> VibeWorkflowGraph:
        """Generate a complete workflow (multi-node graph + all referenced skills)."""
        return self.workflow_factory.generate(brief)

    def workflow_with_report(self, brief: str) -> WorkflowGenerationReport:
        """Like :meth:`workflow` but returns the warnings + skill-id manifest too."""
        return self.workflow_factory.generate_with_report(brief)

    # ------------------------------------------------------------------ execution

    def run(self, skill_id: str, input_data: dict[str, Any]) -> CodeSkillRun:
        """Execute a code skill; runtime auto-repairs and solidifies on failure."""
        return self.code_runtime.run(skill_id, input_data)

    def execute(self, graph: VibeWorkflowGraph, input_data: dict[str, Any]) -> WorkflowRunResult:
        """Execute a workflow; per-node failures are healed by the underlying runtimes."""
        return self.engine.run(graph, input_data)

    # ------------------------------------------------------------------ audit

    def history(self, skill_id: str) -> list[PatchRecord]:
        return self.ledger.history(skill_id)

    def evolution_chain(self, skill_id: str) -> list[dict[str, Any]]:
        return self.ledger.evolution_chain(skill_id)

    def rollback(self, skill_id: str, target_version: int) -> CodeSkill | ESkill:
        return self.ledger.rollback(skill_id, target_version)

    def report(self, skill_id: str | None = None) -> dict[str, Any]:
        return self.ledger.report(skill_id)

    # ------------------------------------------------------------------ list

    def list_code_skills(self) -> list[CodeSkill]:
        return self.code_store.list_code_skills()

    def list_config_skills(self) -> list[ESkill]:
        return self.config_store.list_skills()


def _build_code_patch_generator(llm: LLMClient):  # noqa: ANN201 - Returns concrete class
    """Wrap a vibe-coding ``LLMClient`` so :class:`CodeSkillRuntime` can use it.

    :class:`OpenAICodePatchGenerator` expects an OpenAI-shaped client, but for
    rule-only setups we fall back to :class:`RuleBasedCodePatchGenerator`. We
    avoid taking a hard dep on openai here; the rule-based generator is
    sufficient for the most common KeyError / TypeError repair patterns.
    """
    try:
        from .nl.llm import OpenAILLM

        if isinstance(llm, OpenAILLM):
            return OpenAICodePatchGenerator(api_key=llm.api_key, model=llm.model, base_url=llm.base_url)
    except Exception:  # noqa: BLE001
        pass
    return RuleBasedCodePatchGenerator()
