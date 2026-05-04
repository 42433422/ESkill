"""Gray rollout: shadow / canary / progressive / full / rollback."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from .models import ESkill, SkillVersion


class RolloutPhase(str, Enum):
    FULL = "full"
    SHADOW = "shadow"
    CANARY = "canary"
    PROGRESSIVE = "progressive"
    ROLLBACK = "rollback"


@dataclass(slots=True)
class SelfHealingConfig:
    """Feature gate for deep healing (analysis, sandbox subprocess, rollout)."""

    enabled: bool = False
    sandbox_timeout_seconds: float = 20.0
    rollout_mode: str = "immediate"  # immediate | shadow | canary | progressive
    canary_percent: int = 10
    sandbox_access_layer: bool = False
    progressive_threshold_ok: int = 50
    progressive_percent_steps: tuple[int, ...] = (1, 5, 25, 50, 100)


class RolloutController:
    """Persist rollout intent on `skill.rollout` and route reads to candidate versions."""

    def resolve_execution_version(self, skill: ESkill, input_data: dict[str, Any]) -> SkillVersion:
        rollout = skill.rollout or {}
        phase = str(rollout.get("phase") or RolloutPhase.FULL.value)
        candidate_ver = rollout.get("candidate_version")

        if phase in (RolloutPhase.SHADOW.value,) or candidate_ver in (None, ""):
            return skill.get_active_version()

        if phase in (RolloutPhase.CANARY.value, RolloutPhase.PROGRESSIVE.value):
            if isinstance(candidate_ver, int) and self._should_route_canary(skill, input_data, rollout):
                for v in skill.versions:
                    if v.version == candidate_ver:
                        return v
            return skill.get_active_version()

        return skill.get_active_version()

    def _should_route_canary(self, skill: ESkill, input_data: dict[str, Any], rollout: dict[str, Any]) -> bool:
        force = (input_data.get("_eskill") or {}).get("force_candidate")
        if force is True:
            return True
        if (input_data.get("_eskill") or {}).get("force_active") is True:
            return False
        pct = int(rollout.get("canary_percent") or self._progressive_percent(rollout) or 0)
        if pct <= 0:
            return False
        if pct >= 100:
            return True
        req = str((input_data.get("_eskill") or {}).get("request_key") or input_data.get("request_id") or "")
        h = abs(hash(req + "#" + skill.skill_id)) % 100
        return h < pct

    def _progressive_percent(self, rollout: dict[str, Any]) -> int:
        if str(rollout.get("phase")) != RolloutPhase.PROGRESSIVE.value:
            return int(rollout.get("canary_percent") or 0)
        step = int(rollout.get("progressive_step") or 0)
        steps = SelfHealingConfig().progressive_percent_steps
        if 0 <= step < len(steps):
            return steps[step]
        return int(rollout.get("canary_percent") or 100)

    def begin_rollout(
        self,
        skill: ESkill,
        *,
        candidate_version: int,
        mode: str,
        canary_percent: int = 10,
    ) -> None:
        cfg = SelfHealingConfig()
        pct = int(canary_percent)
        if str(mode).lower() == RolloutPhase.PROGRESSIVE.value:
            pct = int(cfg.progressive_percent_steps[0]) if cfg.progressive_percent_steps else pct
        skill.rollout = {
            "phase": mode,
            "candidate_version": candidate_version,
            "canary_percent": pct,
            "baseline_version": int(skill.active_version),
            "progressive_step": 0,
            "metrics": {"candidate": {"ok": 0, "fail": 0}, "baseline": {"ok": 0, "fail": 0}},
        }

    def record_outcome(self, skill: ESkill, *, used_candidate: bool, success: bool) -> None:
        r = dict(skill.rollout or {})
        metrics = dict(r.get("metrics") or {})
        key = "candidate" if used_candidate else "baseline"
        bucket = dict(metrics.get(key) or {"ok": 0, "fail": 0})
        if success:
            bucket["ok"] = int(bucket.get("ok") or 0) + 1
        else:
            bucket["fail"] = int(bucket.get("fail") or 0) + 1
        metrics[key] = bucket
        r["metrics"] = metrics
        skill.rollout = r

    def maybe_advance_progressive(self, skill: ESkill, cfg: SelfHealingConfig) -> None:
        r = dict(skill.rollout or {})
        if str(r.get("phase")) != RolloutPhase.PROGRESSIVE.value:
            return
        metrics = r.get("metrics") or {}
        c_ok = int((metrics.get("candidate") or {}).get("ok") or 0)
        c_fail = int((metrics.get("candidate") or {}).get("fail") or 0)
        if c_ok + c_fail < cfg.progressive_threshold_ok:
            return
        if c_fail == 0 and c_ok >= cfg.progressive_threshold_ok:
            step = int(r.get("progressive_step") or 0) + 1
            steps = cfg.progressive_percent_steps
            if step >= len(steps):
                self.promote_candidate_to_active(skill)
                return
            r["progressive_step"] = step
            r["canary_percent"] = steps[step]
        skill.rollout = r

    def promote_candidate_to_active(self, skill: ESkill) -> int | None:
        r = dict(skill.rollout or {})
        cv = r.get("candidate_version")
        if not isinstance(cv, int):
            return None
        skill.active_version = cv
        skill.rollout = {
            "phase": RolloutPhase.FULL.value,
            "candidate_version": None,
            "canary_percent": 100,
            "baseline_version": cv,
            "metrics": r.get("metrics") or {},
        }
        return cv

    def rollback(self, skill: ESkill) -> int | None:
        r = dict(skill.rollout or {})
        baseline = r.get("baseline_version")
        skill.rollout = {
            "phase": RolloutPhase.ROLLBACK.value,
            "candidate_version": None,
            "canary_percent": 0,
            "baseline_version": baseline,
            "metrics": r.get("metrics") or {},
        }
        if isinstance(baseline, int):
            skill.active_version = baseline
            return baseline
        return None
