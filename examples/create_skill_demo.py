"""
Demonstrates programmatic creation of a new ESkill and a dry run through the runtime.

Usage:
  python examples/create_skill_demo.py
"""

from __future__ import annotations

import json
from pathlib import Path

from eskill import ESkillRuntime, JsonSkillStore, SkillBlueprint, SkillCreator


def main() -> None:
    registry = Path(__file__).resolve().parents[1] / "data" / "registry_create_demo.json"
    store = JsonSkillStore(registry)

    bp = SkillBlueprint.template_transform(
        skill_id="release-note-draft",
        name="Release Note Draft",
        domain="software release communications",
        template="## ${version}\n\n${summary}\n\nImpact: ${impact}",
        required_fields=["version", "summary", "impact"],
        output_var="note",
        domain_keywords=["release", "版本", "changelog"],
        dynamic_template="## ${version}\n\n${summary}\n\nImpact: ${impact}\n\nDetails: ${details}",
        quality_gate={"min_length": 40, "contains_all": ["##"]},
    )

    skill, created = SkillCreator.ensure(store, bp)
    print(json.dumps({"created": created, "manifest": bp.to_manifest()}, ensure_ascii=False, indent=2))
    print("--- agent brief ---")
    print(bp.to_agent_brief_markdown())

    runtime = ESkillRuntime(store)
    run = runtime.run(
        skill.skill_id,
        {
            "version": "1.2.0",
            "summary": "Fix startup crash on Windows.",
            "impact": "All desktop users should upgrade.",
        },
        solidify=False,
    )
    print("--- run ---")
    print(json.dumps(run.to_dict(), ensure_ascii=False, indent=2))
    print(f"Registry: {registry}")


if __name__ == "__main__":
    main()
