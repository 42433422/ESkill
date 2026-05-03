from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from .models import ESkill, EvolutionEvent, SkillHealthReport, SkillRun


class SQLiteSkillStore:
    """SQLite backend for ESkill with proper transaction support."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init_db(self) -> None:
        with self._lock:
            with self._get_conn() as conn:
                conn.executescript("""
                    CREATE TABLE IF NOT EXISTS skills (
                        skill_id TEXT PRIMARY KEY,
                        data TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS runs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        skill_id TEXT NOT NULL,
                        data TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                    CREATE TABLE IF NOT EXISTS events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        skill_id TEXT NOT NULL,
                        data TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                    CREATE TABLE IF NOT EXISTS health_reports (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        skill_id TEXT NOT NULL,
                        data TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                    CREATE INDEX IF NOT EXISTS idx_runs_skill_id ON runs(skill_id);
                    CREATE INDEX IF NOT EXISTS idx_events_skill_id ON events(skill_id);
                    CREATE INDEX IF NOT EXISTS idx_health_skill_id ON health_reports(skill_id);
                """)

    def list_skills(self) -> list[ESkill]:
        with self._get_conn() as conn:
            rows = conn.execute("SELECT data FROM skills").fetchall()
            return [ESkill.from_dict(json.loads(row["data"])) for row in rows]

    def get_skill(self, skill_id: str) -> ESkill:
        with self._get_conn() as conn:
            row = conn.execute("SELECT data FROM skills WHERE skill_id = ?", (skill_id,)).fetchone()
            if not row:
                raise KeyError(f"Skill not found: {skill_id}")
            return ESkill.from_dict(json.loads(row["data"]))

    def has_skill(self, skill_id: str) -> bool:
        with self._get_conn() as conn:
            row = conn.execute("SELECT 1 FROM skills WHERE skill_id = ?", (skill_id,)).fetchone()
            return row is not None

    def save_skill(self, skill: ESkill) -> None:
        with self._lock:
            with self._get_conn() as conn:
                conn.execute(
                    """
                    INSERT INTO skills (skill_id, data) VALUES (?, ?)
                    ON CONFLICT(skill_id) DO UPDATE SET data = excluded.data
                    """,
                    (skill.skill_id, json.dumps(skill.to_dict(), ensure_ascii=False)),
                )

    def append_run(self, run: SkillRun) -> None:
        with self._lock:
            with self._get_conn() as conn:
                conn.execute(
                    "INSERT INTO runs (skill_id, data) VALUES (?, ?)",
                    (run.skill_id, json.dumps(run.to_dict(), ensure_ascii=False)),
                )

    def list_runs(self, skill_id: str | None = None) -> list[dict[str, Any]]:
        with self._get_conn() as conn:
            if skill_id is None:
                rows = conn.execute("SELECT data FROM runs").fetchall()
            else:
                rows = conn.execute(
                    "SELECT data FROM runs WHERE skill_id = ?", (skill_id,)
                ).fetchall()
            return [json.loads(row["data"]) for row in rows]

    def append_event(self, event: EvolutionEvent) -> None:
        with self._lock:
            with self._get_conn() as conn:
                conn.execute(
                    "INSERT INTO events (skill_id, data) VALUES (?, ?)",
                    (event.skill_id, json.dumps(event.to_dict(), ensure_ascii=False)),
                )

    def list_events(
        self, skill_id: str | None = None, event_type: str | None = None
    ) -> list[dict[str, Any]]:
        with self._get_conn() as conn:
            query = "SELECT data FROM events WHERE 1=1"
            params: list[Any] = []
            if skill_id is not None:
                query += " AND skill_id = ?"
                params.append(skill_id)
            if event_type is not None:
                data = conn.execute(query, params).fetchall()
                events = [json.loads(row["data"]) for row in data]
                return [e for e in events if e.get("event_type") == event_type]
            rows = conn.execute(query, params).fetchall()
            return [json.loads(row["data"]) for row in rows]

    def append_health_report(self, report: SkillHealthReport) -> None:
        with self._lock:
            with self._get_conn() as conn:
                conn.execute(
                    "INSERT INTO health_reports (skill_id, data) VALUES (?, ?)",
                    (report.skill_id, json.dumps(report.to_dict(), ensure_ascii=False)),
                )

    def list_health_reports(self, skill_id: str | None = None) -> list[dict[str, Any]]:
        with self._get_conn() as conn:
            if skill_id is None:
                rows = conn.execute("SELECT data FROM health_reports").fetchall()
            else:
                rows = conn.execute(
                    "SELECT data FROM health_reports WHERE skill_id = ?", (skill_id,)
                ).fetchall()
            return [json.loads(row["data"]) for row in rows]

    def get_version_diff(self, skill_id: str, v1: int, v2: int | None = None) -> dict[str, Any]:
        skill = self.get_skill(skill_id)
        versions = sorted(skill.versions, key=lambda v: v.version)
        version_map = {v.version: v for v in versions}
        if v1 not in version_map:
            raise ValueError(f"Version {v1} not found for skill {skill_id}")
        if v2 is None:
            if len(versions) < 2:
                return {"added": {}, "removed": {}, "changed": {}, "message": "No previous version"}
            v2 = max(version_map.keys())
        if v2 not in version_map:
            raise ValueError(f"Version {v2} not found for skill {skill_id}")
        logic1 = version_map[v1].static_logic
        logic2 = version_map[v2].static_logic
        added = {k: v for k, v in logic2.items() if k not in logic1}
        removed = {k: v for k, v in logic1.items() if k not in logic2}
        changed = {k: {"old": logic1[k], "new": logic2[k]} for k in logic1 if k in logic2 and logic1[k] != logic2[k]}
        return {"added": added, "removed": removed, "changed": changed, "from_version": v1, "to_version": v2}

    def set_active_version(self, skill_id: str, version: int) -> ESkill:
        with self._lock:
            skill = self.get_skill(skill_id)
            if not any(v.version == version for v in skill.versions):
                raise ValueError(f"Skill {skill_id} does not have version {version}")
            skill.active_version = version
            self.save_skill(skill)
            return skill
