from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class StrategyPreset:
    name: str
    innovation_weight: float
    optimization_weight: float
    repair_weight: float
    allow_exploration: bool = True
    tighten_quality_gate: bool = False

    def to_dict(self) -> dict[str, float | bool | str]:
        return {
            "name": self.name,
            "innovation_weight": self.innovation_weight,
            "optimization_weight": self.optimization_weight,
            "repair_weight": self.repair_weight,
            "allow_exploration": self.allow_exploration,
            "tighten_quality_gate": self.tighten_quality_gate,
        }


STRATEGY_PRESETS: dict[str, StrategyPreset] = {
    "balanced": StrategyPreset("balanced", 0.5, 0.3, 0.2),
    "innovate": StrategyPreset("innovate", 0.8, 0.1, 0.1, allow_exploration=True),
    "harden": StrategyPreset("harden", 0.2, 0.4, 0.4, tighten_quality_gate=True),
    "repair-only": StrategyPreset(
        "repair-only",
        0.0,
        0.0,
        1.0,
        allow_exploration=False,
        tighten_quality_gate=True,
    ),
}


def get_strategy_preset(name: str | None) -> StrategyPreset:
    return STRATEGY_PRESETS.get(name or "balanced", STRATEGY_PRESETS["balanced"])
