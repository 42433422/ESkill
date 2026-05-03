from __future__ import annotations

import json
from pathlib import Path

from .models import ESkill, SkillVersion, TriggerPolicy
from .runtime import ESkillRuntime
from .store import JsonSkillStore


def build_demo_store(path: Path) -> JsonSkillStore:
    store = JsonSkillStore(path)
    store.save_skill(
        ESkill(
            skill_id="brief-writer",
            name="Brief Writer",
            domain="write short product briefs",
            active_version=1,
            versions=[
                SkillVersion(
                    version=1,
                    static_logic={
                        "type": "template_transform",
                        "template": "Brief: ${topic}",
                        "dynamic_template": "Brief: ${topic}. Details: ${details}",
                        "required_fields": ["topic"],
                        "output_var": "brief",
                        "domain_keywords": ["ESkill", "architecture", "brief"],
                        "allow_steps": True,
                    },
                    trigger_policy=TriggerPolicy(),
                    quality_gate={"min_length": 80},
                )
            ],
        )
    )
    return store


def main() -> None:
    registry = Path(__file__).resolve().parents[2] / "data" / "registry.json"
    store = build_demo_store(registry)
    runtime = ESkillRuntime(store)

    static_run = runtime.run(
        "brief-writer",
        {"topic": "ESkill architecture", "details": "static first, dynamic only when needed"},
        quality_gate={"min_length": 1},
        solidify=False,
    )
    dynamic_run = runtime.run(
        "brief-writer",
        {"topic": "ESkill architecture", "details": "static first, dynamic only when needed"},
    )

    print(json.dumps(static_run.to_dict(), ensure_ascii=False, indent=2))
    print(json.dumps(dynamic_run.to_dict(), ensure_ascii=False, indent=2))
    print(f"Registry written to: {registry}")


if __name__ == "__main__":
    main()
