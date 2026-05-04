"""Fault layering: access vs logic vs architecture."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class FaultLayer(str, Enum):
    ACCESS = "access"
    LOGIC = "logic"
    ARCHITECTURE = "architecture"


class FaultType(str, Enum):
    MISSING_FIELD = "missing_field"
    INVALID_FORMAT = "invalid_format"
    QUALITY_CONTRACT = "quality_contract"
    BRANCH_OR_CONDITION = "branch_or_condition"
    STATE_TRANSITION = "state_transition"
    FORMULA_OR_CALC = "formula_or_calc"
    TIMEOUT = "timeout"
    DEADLOCK = "deadlock"
    CONCURRENCY = "concurrency"
    DEPENDENCY = "dependency"
    RESOURCE_EXHAUSTION = "resource_exhaustion"
    UNKNOWN = "unknown"


@dataclass(slots=True)
class FaultSignal:
    """Single observation used for classification."""

    name: str
    value: Any = None
    source: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class FaultDiagnosis:
    layer: FaultLayer
    fault_type: FaultType
    confidence: float
    reason_codes: list[str] = field(default_factory=list)
    notes: str = ""
    signals: list[FaultSignal] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "layer": self.layer.value,
            "fault_type": self.fault_type.value,
            "confidence": self.confidence,
            "reason_codes": list(self.reason_codes),
            "notes": self.notes,
            "signals": [s.to_dict() for s in self.signals],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> FaultDiagnosis:
        sigs: list[FaultSignal] = []
        for s in raw.get("signals") or []:
            if isinstance(s, dict):
                sigs.append(
                    FaultSignal(
                        name=str(s.get("name") or ""),
                        value=s.get("value"),
                        source=str(s.get("source") or ""),
                    )
                )
            else:
                sigs.append(FaultSignal(name=str(s)))
        return cls(
            layer=FaultLayer(str(raw.get("layer") or FaultLayer.ACCESS.value)),
            fault_type=FaultType(str(raw.get("fault_type") or FaultType.UNKNOWN.value)),
            confidence=float(raw.get("confidence") or 0.0),
            reason_codes=[str(x) for x in raw.get("reason_codes") or []],
            notes=str(raw.get("notes") or ""),
            signals=sigs,
        )


_ARCH_PATTERNS: list[tuple[str, FaultType]] = [
    ("timeout", FaultType.TIMEOUT),
    ("timed out", FaultType.TIMEOUT),
    ("deadlock", FaultType.DEADLOCK),
    ("lock", FaultType.CONCURRENCY),
    ("race", FaultType.CONCURRENCY),
    ("connection", FaultType.DEPENDENCY),
    ("503", FaultType.DEPENDENCY),
    ("502", FaultType.DEPENDENCY),
    ("memory", FaultType.RESOURCE_EXHAUSTION),
    ("oom", FaultType.RESOURCE_EXHAUSTION),
    ("circuit", FaultType.DEPENDENCY),
]


class FaultClassifier:
    """Classify failures into access / logic / architecture layers."""

    @staticmethod
    def classify(
        *,
        trigger_reason: str,
        error: Exception | None,
        quality_report: dict[str, Any] | None,
        static_logic: dict[str, Any],
        input_data: dict[str, Any] | None = None,
    ) -> FaultDiagnosis:
        signals: list[FaultSignal] = [
            FaultSignal("trigger_reason", trigger_reason, "runtime"),
        ]
        err_text = (str(error) if error else "").lower()
        q_issues = [str(x).lower() for x in (quality_report or {}).get("issues") or []]

        if error and "missing required fields" in err_text:
            return FaultDiagnosis(
                layer=FaultLayer.ACCESS,
                fault_type=FaultType.MISSING_FIELD,
                confidence=0.95,
                reason_codes=["missing_required_fields"],
                notes=str(error),
                signals=signals + [FaultSignal("exception", err_text[:500], "error")],
            )

        if error and _match_arch(err_text):
            ft = _arch_fault_type(err_text)
            return FaultDiagnosis(
                layer=FaultLayer.ARCHITECTURE,
                fault_type=ft,
                confidence=0.85,
                reason_codes=["error_message_arch_signal"],
                notes=str(error)[:500],
                signals=signals + [FaultSignal("exception", err_text[:500], "error")],
            )

        if static_logic.get("healing_hints", {}).get("layer") == "logic":
            return FaultDiagnosis(
                layer=FaultLayer.LOGIC,
                fault_type=FaultType.BRANCH_OR_CONDITION,
                confidence=0.75,
                reason_codes=["explicit_logic_hint"],
                notes="static_logic.healing_hints.layer=logic",
                signals=signals,
            )

        if static_logic.get("healing_hints", {}).get("layer") == "architecture":
            return FaultDiagnosis(
                layer=FaultLayer.ARCHITECTURE,
                fault_type=FaultType.TIMEOUT,
                confidence=0.75,
                reason_codes=["explicit_arch_hint"],
                notes="static_logic.healing_hints.layer=architecture",
                signals=signals,
            )

        if trigger_reason == "quality_gate" and quality_report:
            for issue in q_issues:
                if issue.startswith("missing_key:") or issue.startswith("missing_text:"):
                    return FaultDiagnosis(
                        layer=FaultLayer.ACCESS,
                        fault_type=FaultType.QUALITY_CONTRACT,
                        confidence=0.8,
                        reason_codes=["quality_contract_output"],
                        notes="quality gate failed on output contract",
                        signals=signals + [FaultSignal("quality_issues", q_issues, "quality")],
                    )
            if any("min_length" in i for i in q_issues):
                return FaultDiagnosis(
                    layer=FaultLayer.ACCESS,
                    fault_type=FaultType.QUALITY_CONTRACT,
                    confidence=0.7,
                    reason_codes=["quality_min_length"],
                    notes="min_length contract",
                    signals=signals + [FaultSignal("quality_issues", q_issues, "quality")],
                )

        if trigger_reason == "quality_gate":
            return FaultDiagnosis(
                layer=FaultLayer.LOGIC,
                fault_type=FaultType.BRANCH_OR_CONDITION,
                confidence=0.55,
                reason_codes=["quality_gate_generic"],
                notes="quality failed; likely semantic / branch issue",
                signals=signals + [FaultSignal("quality_issues", q_issues, "quality")],
            )

        if trigger_reason == "error" and error:
            if _looks_like_json_or_parse_error(err_text):
                return FaultDiagnosis(
                    layer=FaultLayer.ACCESS,
                    fault_type=FaultType.INVALID_FORMAT,
                    confidence=0.7,
                    reason_codes=["parse_or_format"],
                    notes=str(error)[:500],
                    signals=signals + [FaultSignal("exception", err_text[:500], "error")],
                )
            return FaultDiagnosis(
                layer=FaultLayer.LOGIC,
                fault_type=FaultType.UNKNOWN,
                confidence=0.5,
                reason_codes=["runtime_error_generic"],
                notes=str(error)[:500],
                signals=signals + [FaultSignal("exception", err_text[:500], "error")],
            )

        if trigger_reason == "force_dynamic":
            return FaultDiagnosis(
                layer=FaultLayer.ACCESS,
                fault_type=FaultType.QUALITY_CONTRACT,
                confidence=0.4,
                reason_codes=["forced_dynamic"],
                notes="dynamic phase forced by policy",
                signals=signals,
            )

        _ = input_data  # reserved for future signals
        return FaultDiagnosis(
            layer=FaultLayer.ACCESS,
            fault_type=FaultType.UNKNOWN,
            confidence=0.3,
            reason_codes=["fallback_access"],
            notes="unclassified; default to access layer",
            signals=signals,
        )


def _match_arch(text: str) -> bool:
    return any(p in text for p, _ in _ARCH_PATTERNS)


def _arch_fault_type(text: str) -> FaultType:
    for pat, ft in _ARCH_PATTERNS:
        if pat in text:
            return ft
    return FaultType.UNKNOWN


def _looks_like_json_or_parse_error(text: str) -> bool:
    return any(
        x in text
        for x in (
            "json",
            "decode",
            "parse",
            "invalid literal",
            "malformed",
            "unexpected token",
        )
    )
