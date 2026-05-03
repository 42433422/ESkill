from __future__ import annotations

from collections import Counter
from typing import Any

from .models import EvolutionEvent
from .store import JsonSkillStore


class AuditTrail:
    """Read-oriented helper for evolution events stored by ESkillRuntime."""

    def __init__(self, store: JsonSkillStore):
        self.store = store

    def list_events(
        self, skill_id: str | None = None, event_type: str | None = None
    ) -> list[EvolutionEvent]:
        return [
            EvolutionEvent.from_dict(raw)
            for raw in self.store.list_events(skill_id=skill_id, event_type=event_type)
        ]

    def summarize(self, skill_id: str | None = None) -> dict[str, Any]:
        events = self.store.list_events(skill_id=skill_id)
        by_type = Counter(str(event.get("event_type") or "") for event in events)
        by_strategy = Counter(
            str(event.get("strategy") or "none") for event in events if event.get("strategy")
        )
        solidified_versions = [
            event.get("solidified_version")
            for event in events
            if event.get("solidified_version") is not None
        ]
        return {
            "total_events": len(events),
            "by_type": dict(by_type),
            "by_strategy": dict(by_strategy),
            "solidified_versions": solidified_versions,
        }
