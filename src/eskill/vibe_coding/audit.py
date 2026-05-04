"""Patch ledger: history, evolution chain, one-click rollback.

The eskill prototype already records :class:`SkillRun` and ``EvolutionEvent``
inside the per-store JSON / SQLite. ``PatchLedger`` sits one level above and
provides the ergonomic "show me everything that ever happened to this skill"
view that vibe-coding callers expect, plus the rollback semantics required
to undo a regression with a single call.

The ledger is layered over the underlying :class:`JsonSkillStore` and
:class:`JsonCodeSkillStore`; nothing is duplicated. We just project the
existing rows into a friendlier shape.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

from ..code import CodeSkill, JsonCodeSkillStore
from ..models import ESkill
from ..store import JsonSkillStore


@dataclass(slots=True)
class PatchRecord:
    """One repair / evolution moment in a skill's lifetime."""

    skill_id: str
    layer: str  # "config" | "code"
    version: int  # version that resulted from this patch (or active version)
    stage: str
    summary: str
    diff: dict[str, Any] = field(default_factory=dict)
    diagnosis: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class PatchLedger:
    """Read-only / mutating utility for vibe-coding's audit story.

    ``code_store`` is required to operate on code-layer skills; ``config_store``
    is required for config-layer skills. Either may be ``None`` if the caller
    has only one layer.
    """

    def __init__(
        self,
        *,
        code_store: JsonCodeSkillStore | None = None,
        config_store: JsonSkillStore | None = None,
    ):
        if code_store is None and config_store is None:
            raise ValueError("at least one of code_store / config_store is required")
        self.code_store = code_store
        self.config_store = config_store

    # ------------------------------------------------------------------ history

    def history(self, skill_id: str) -> list[PatchRecord]:
        """Return chronological patch history across whichever store has the skill."""
        records: list[PatchRecord] = []
        if self.code_store is not None and self.code_store.has_code_skill(skill_id):
            records.extend(self._code_history(skill_id))
        if self.config_store is not None and self.config_store.has_skill(skill_id):
            records.extend(self._config_history(skill_id))
        records.sort(key=lambda r: r.created_at)
        return records

    def evolution_chain(self, skill_id: str) -> list[dict[str, Any]]:
        """Return v1 → v2 → ... → vN summaries for the skill's versions."""
        if self.code_store is not None and self.code_store.has_code_skill(skill_id):
            skill = self.code_store.get_code_skill(skill_id)
            chain: list[dict[str, Any]] = []
            for v in sorted(skill.versions, key=lambda x: x.version):
                chain.append(
                    {
                        "version": v.version,
                        "function_name": v.function_name,
                        "source_run_id": v.source_run_id,
                        "test_cases": len(v.test_cases),
                        "active": v.version == skill.active_version,
                        "created_at": v.created_at,
                    }
                )
            return chain
        if self.config_store is not None and self.config_store.has_skill(skill_id):
            skill = self.config_store.get_skill(skill_id)
            return [
                {
                    "version": v.version,
                    "logic_type": v.static_logic.get("type"),
                    "source_run_id": v.source_run_id,
                    "active": v.version == skill.active_version,
                    "created_at": v.created_at,
                }
                for v in sorted(skill.versions, key=lambda x: x.version)
            ]
        raise KeyError(f"skill not found in either store: {skill_id!r}")

    # ----------------------------------------------------------------- rollback

    def rollback(self, skill_id: str, target_version: int) -> CodeSkill | ESkill:
        """Activate ``target_version`` for ``skill_id``. Returns the updated skill."""
        if self.code_store is not None and self.code_store.has_code_skill(skill_id):
            skill = self.code_store.get_code_skill(skill_id)
            if not any(v.version == target_version for v in skill.versions):
                raise ValueError(f"version {target_version} not in code skill {skill_id!r}")
            skill.active_version = int(target_version)
            self.code_store.save_code_skill(skill)
            return skill
        if self.config_store is not None and self.config_store.has_skill(skill_id):
            self.config_store.set_active_version(skill_id, int(target_version))
            return self.config_store.get_skill(skill_id)
        raise KeyError(f"skill not found in either store: {skill_id!r}")

    # ---------------------------------------------------------------- reporters

    def report(self, skill_id: str | None = None) -> dict[str, Any]:
        """Aggregate health metrics across all skills (or one if ``skill_id`` given)."""
        all_skills: list[tuple[str, str]] = []  # (layer, skill_id)
        if self.code_store is not None:
            for s in self.code_store.list_code_skills():
                all_skills.append(("code", s.skill_id))
        if self.config_store is not None:
            for s in self.config_store.list_skills():
                all_skills.append(("config", s.skill_id))
        if skill_id is not None:
            all_skills = [s for s in all_skills if s[1] == skill_id]
        out: dict[str, Any] = {"skills": []}
        for layer, sid in all_skills:
            history = self.history(sid)
            healed = sum(1 for r in history if r.stage in ("solidified", "dynamic", "healed"))
            failed = sum(1 for r in history if r.error)
            out["skills"].append(
                {
                    "skill_id": sid,
                    "layer": layer,
                    "versions": len(self.evolution_chain(sid)),
                    "patches": len(history),
                    "healed": healed,
                    "failed": failed,
                    "active_version": self._active_version(layer, sid),
                }
            )
        out["totals"] = {
            "skills": len(out["skills"]),
            "patches": sum(s["patches"] for s in out["skills"]),
            "healed": sum(s["healed"] for s in out["skills"]),
            "failed": sum(s["failed"] for s in out["skills"]),
        }
        return out

    # ------------------------------------------------------------------ helpers

    def _active_version(self, layer: str, skill_id: str) -> int:
        if layer == "code" and self.code_store is not None:
            return self.code_store.get_code_skill(skill_id).active_version
        if layer == "config" and self.config_store is not None:
            return self.config_store.get_skill(skill_id).active_version
        return 0

    def _code_history(self, skill_id: str) -> Iterable[PatchRecord]:
        assert self.code_store is not None
        for run in self.code_store.list_code_runs(skill_id):
            patch = run.get("patch") or {}
            diag = run.get("diagnosis") or {}
            yield PatchRecord(
                skill_id=skill_id,
                layer="code",
                version=int(patch.get("solidified_version") or 0),
                stage=str(run.get("stage") or ""),
                summary=str(patch.get("diff_summary") or patch.get("reason") or ""),
                diff={
                    "original_code": patch.get("original_code") or "",
                    "patched_code": patch.get("patched_code") or "",
                    "reasoning": patch.get("llm_reasoning") or "",
                },
                diagnosis=dict(diag) if isinstance(diag, dict) else {},
                error=str(run.get("error") or ""),
                created_at=str(run.get("started_at") or run.get("completed_at") or ""),
            )

    def _config_history(self, skill_id: str) -> Iterable[PatchRecord]:
        assert self.config_store is not None
        for run in self.config_store.list_runs(skill_id):
            patch = run.get("patch") or {}
            diag = run.get("diagnosis") or {}
            yield PatchRecord(
                skill_id=skill_id,
                layer="config",
                version=0,
                stage=str(run.get("stage") or ""),
                summary=str(patch.get("reason") or ""),
                diff={"changes": patch.get("changes") or {}},
                diagnosis=dict(diag) if isinstance(diag, dict) else {},
                error=str(run.get("error") or ""),
                created_at=str(run.get("started_at") or run.get("completed_at") or ""),
            )
