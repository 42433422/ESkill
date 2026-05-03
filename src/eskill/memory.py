from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any
from uuid import uuid4

from .models import now_iso
from .store import JsonSkillStore


@dataclass(slots=True)
class MemoryRecord:
    layer: str
    content: dict[str, Any]
    skill_id: str = ""
    tags: list[str] = field(default_factory=list)
    memory_id: str = field(default_factory=lambda: uuid4().hex)
    created_at: str = field(default_factory=now_iso)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "MemoryRecord":
        return cls(
            layer=str(raw.get("layer") or ""),
            content=dict(raw.get("content") or {}),
            skill_id=str(raw.get("skill_id") or ""),
            tags=[str(x) for x in raw.get("tags") or []],
            memory_id=str(raw.get("memory_id") or uuid4().hex),
            created_at=str(raw.get("created_at") or now_iso()),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class LayeredMemoryStore:
    VALID_LAYERS = {"L0", "L1", "L2", "L3", "L4"}

    def __init__(self, store: JsonSkillStore):
        self.store = store

    def remember(
        self,
        layer: str,
        content: dict[str, Any],
        *,
        skill_id: str = "",
        tags: list[str] | None = None,
    ) -> MemoryRecord:
        if layer not in self.VALID_LAYERS:
            raise ValueError(f"Unknown memory layer: {layer}")
        record = MemoryRecord(layer=layer, content=dict(content), skill_id=skill_id, tags=tags or [])
        self.store.append_record("memories", record.to_dict())
        return record

    def search(
        self,
        *,
        skill_id: str | None = None,
        layer: str | None = None,
        query: str = "",
        limit: int = 10,
    ) -> list[MemoryRecord]:
        rows = self.store.list_records("memories")
        if skill_id is not None:
            rows = [row for row in rows if row.get("skill_id") in {"", skill_id}]
        if layer is not None:
            rows = [row for row in rows if row.get("layer") == layer]
        if query:
            q = query.lower()
            rows = [
                row
                for row in rows
                if q in json.dumps(row, ensure_ascii=False).lower()
                or any(q in str(tag).lower() for tag in row.get("tags") or [])
            ]
        return [MemoryRecord.from_dict(row) for row in rows[-limit:]]
