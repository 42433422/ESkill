"""Structured patch planning: rules + optional LLM + architecture overlays."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Protocol

from .architecture import ArchitectureAdjuster, ArchitectureProfile
from .diagnostics import FaultDiagnosis, FaultLayer
from .models import DynamicPatch


class PatchProposer(Protocol):
    def propose(
        self,
        *,
        reason: str,
        logic: dict[str, Any],
        input_data: dict[str, Any],
        history: list[dict[str, Any]] | None = None,
        error: Exception | None = None,
    ) -> DynamicPatch:
        ...


@dataclass(slots=True)
class PatchProposal:
    """LLM / planner output envelope (also used when falling back to rules)."""

    target_layer: str
    risk: str
    validation_plan: list[str]
    patch: DynamicPatch
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_layer": self.target_layer,
            "risk": self.risk,
            "validation_plan": list(self.validation_plan),
            "patch": self.patch.to_dict(),
            "notes": self.notes,
        }


class PatchPlanner:
    """Combine diagnosis + analysis into a concrete DynamicPatch."""

    def __init__(
        self,
        *,
        rule_proposer: PatchProposer | None = None,
        llm_generator: Any | None = None,
    ):
        self._rules = rule_proposer
        self._llm = llm_generator

    def plan(
        self,
        *,
        diagnosis: FaultDiagnosis,
        analysis_report: dict[str, Any],
        reason: str,
        logic: dict[str, Any],
        input_data: dict[str, Any],
        history: list[dict[str, Any]] | None,
        error: Exception | None,
        quality_report: dict[str, Any] | None,
    ) -> PatchProposal:
        validation_plan = list(analysis_report.get("invariants_suggested") or [])
        validation_plan.append("Run sandbox replay + quality gate before solidify.")

        if diagnosis.layer == FaultLayer.ARCHITECTURE:
            base = self._base_patch(reason, logic, input_data, history, error, quality_report)
            merged = _overlay_architecture_resilience(base, diagnosis, logic)
            return PatchProposal(
                target_layer=diagnosis.layer.value,
                risk="medium",
                validation_plan=[
                    *validation_plan,
                    "Verify retry/backoff does not amplify downstream load (anti-snowball).",
                ],
                patch=merged,
                notes="Architecture-layer patch prefers resilience metadata over logic edits.",
            )

        if diagnosis.layer == FaultLayer.LOGIC and self._llm is not None:
            try:
                patch = self._llm.generate_patch(
                    reason=reason,
                    logic=logic,
                    input_data=input_data,
                    history=history,
                    error=error,
                    quality_report=quality_report,
                )
                patch = _ensure_patch_metadata(patch, diagnosis, analysis_report)
                return PatchProposal(
                    target_layer=diagnosis.layer.value,
                    risk="high",
                    validation_plan=[*validation_plan, "Diff replay against golden cases."],
                    patch=patch,
                    notes="LLM-generated logic patch; must pass sandbox.",
                )
            except Exception:
                pass

        base = self._base_patch(reason, logic, input_data, history, error, quality_report)
        base = _ensure_patch_metadata(base, diagnosis, analysis_report)
        return PatchProposal(
            target_layer=diagnosis.layer.value,
            risk="low" if diagnosis.layer == FaultLayer.ACCESS else "medium",
            validation_plan=validation_plan,
            patch=base,
            notes="Rule-based or LLM-fallback patch.",
        )

    def _base_patch(
        self,
        reason: str,
        logic: dict[str, Any],
        input_data: dict[str, Any],
        history: list[dict[str, Any]] | None,
        error: Exception | None,
        quality_report: dict[str, Any] | None,
    ) -> DynamicPatch:
        if self._rules is not None:
            return self._rules.propose(
                reason=reason,
                logic=logic,
                input_data=input_data,
                history=history,
                error=error,
            )
        from .models import DynamicPatch as DP

        meta: dict[str, Any] = {"adapted_for": reason, "planner": "noop"}
        if quality_report:
            meta["quality_report"] = quality_report
        return DP(reason=reason, changes={"metadata": meta})


def _ensure_patch_metadata(
    patch: DynamicPatch, diagnosis: FaultDiagnosis, analysis_report: dict[str, Any]
) -> DynamicPatch:
    changes = deepcopy(patch.changes)
    meta = dict(changes.get("metadata") or {})
    meta["diagnosis"] = diagnosis.to_dict()
    meta["analysis_headline"] = {
        "logic_type": analysis_report.get("logic_type"),
        "risk_flags": analysis_report.get("risk_flags"),
    }
    changes["metadata"] = meta
    return DynamicPatch(reason=patch.reason, changes=changes)


def _overlay_architecture_resilience(
    patch: DynamicPatch, diagnosis: FaultDiagnosis, logic: dict[str, Any]
) -> DynamicPatch:
    changes = deepcopy(patch.changes)
    raw_profile = logic.get("architecture_profile")
    current = ArchitectureProfile.from_dict(raw_profile) if raw_profile else None
    new_profile = ArchitectureAdjuster.adjust(diagnosis, current)
    encoded = ArchitectureAdjuster.encode_into_logic(dict(logic), new_profile)
    changes["architecture_profile"] = encoded["architecture_profile"]
    meta = dict(changes.get("metadata") or {})
    meta["resilience"] = dict(encoded.get("metadata", {}).get("resilience") or {})
    meta["resilience"]["fault_type"] = diagnosis.fault_type.value
    meta["resilience"]["layer"] = diagnosis.layer.value
    changes["metadata"] = meta
    return DynamicPatch(reason=patch.reason, changes=changes)
