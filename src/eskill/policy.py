from __future__ import annotations

from .models import AdaptivePolicyState, TriggerPolicy, now_iso
from .store import JsonSkillStore
from .strategy import StrategyPreset, get_strategy_preset


class AdaptivePolicyEngine:
    """
    Lightweight policy learner backed by evolution events.
    It remains compatible with the existing TriggerPolicy consumed by ESkillRuntime.
    """

    SUCCESS_EVENTS = {"static_completed", "validation_passed", "version_solidified", "dynamic_completed"}
    FAILURE_EVENTS = {"validation_failed", "rollback", "domain_rejected", "static_error"}

    def __init__(self, store: JsonSkillStore):
        self.store = store

    def learn(self, skill_id: str) -> AdaptivePolicyState:
        attempts = {"on_error": 0, "on_quality": 0, "force_dynamic": 0}
        successes = {"on_error": 0, "on_quality": 0, "force_dynamic": 0}

        for event in self.store.list_events(skill_id=skill_id):
            signal = str(event.get("trigger_signal") or "")
            action = self._action_for_signal(signal)
            if not action:
                continue
            attempts[action] += 1
            if str(event.get("event_type") or "") in self.SUCCESS_EVENTS:
                successes[action] += 1

        q_values = {
            action: (successes[action] / attempts[action] if attempts[action] else 0.0)
            for action in attempts
        }
        state = AdaptivePolicyState(
            skill_id=skill_id,
            q_values=q_values,
            attempts=attempts,
            successes=successes,
            exploration_rate=self._exploration_rate(q_values),
            updated_at=now_iso(),
        )
        self.store.append_record("policy_states", state.to_dict())
        return state

    def recommend_trigger_policy(
        self,
        skill_id: str,
        *,
        strategy: str | StrategyPreset = "balanced",
    ) -> TriggerPolicy:
        state = self.learn(skill_id)
        preset = get_strategy_preset(strategy if isinstance(strategy, str) else strategy.name)
        return self.to_trigger_policy(state, preset)

    def to_trigger_policy(
        self,
        state: AdaptivePolicyState,
        strategy: StrategyPreset | None = None,
    ) -> TriggerPolicy:
        preset = strategy or get_strategy_preset("balanced")
        on_error = state.q_values.get("on_error", 0.0) >= 0.2 or preset.repair_weight >= 0.4
        on_quality = state.q_values.get("on_quality", 0.0) >= 0.2 or preset.optimization_weight >= 0.3
        force_dynamic = bool(
            preset.allow_exploration
            and preset.innovation_weight >= 0.75
            and state.exploration_rate >= 0.05
        )
        return TriggerPolicy(
            on_error=on_error,
            on_quality_below_threshold=on_quality,
            force_dynamic=force_dynamic,
        )

    def choose_strategy(self, skill_id: str) -> StrategyPreset:
        events = self.store.list_events(skill_id=skill_id)
        if not events:
            return get_strategy_preset("balanced")
        recent = events[-10:]
        failures = sum(1 for event in recent if event.get("event_type") in self.FAILURE_EVENTS)
        successes = sum(1 for event in recent if event.get("event_type") in self.SUCCESS_EVENTS)
        if failures >= 3:
            return get_strategy_preset("repair-only")
        if successes >= 5 and failures == 0:
            return get_strategy_preset("innovate")
        if failures > successes:
            return get_strategy_preset("harden")
        return get_strategy_preset("balanced")

    def _action_for_signal(self, signal: str) -> str:
        if signal == "error":
            return "on_error"
        if signal == "quality_gate":
            return "on_quality"
        if signal == "force_dynamic":
            return "force_dynamic"
        return ""

    def _exploration_rate(self, q_values: dict[str, float]) -> float:
        if not q_values or not any(q_values.values()):
            return 0.2
        avg = sum(q_values.values()) / len(q_values)
        return round(max(0.02, min(0.3, 0.2 * avg)), 4)
