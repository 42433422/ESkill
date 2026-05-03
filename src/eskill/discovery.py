from __future__ import annotations

from typing import Any

from .store import JsonSkillStore


class SkillDiscovery:
    """Skill search, tagging, and description matching."""

    def __init__(self, store: JsonSkillStore):
        self.store = store

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        query_lower = query.lower()
        results: list[dict[str, Any]] = []
        for skill in self.store.list_skills():
            score = 0.0
            if query_lower in skill.name.lower():
                score += 3.0
            if query_lower in skill.domain.lower():
                score += 2.0
            if query_lower in skill.skill_id.lower():
                score += 1.5
            for version in skill.versions:
                for keyword in version.static_logic.get("domain_keywords", []):
                    if query_lower in str(keyword).lower():
                        score += 1.0
            if score > 0:
                results.append({
                    "skill_id": skill.skill_id,
                    "name": skill.name,
                    "domain": skill.domain,
                    "active_version": skill.active_version,
                    "total_versions": len(skill.versions),
                    "relevance_score": round(score, 2),
                })
        results.sort(key=lambda r: r["relevance_score"], reverse=True)
        return results[:limit]

    def list_by_domain(self, domain: str, limit: int = 10) -> list[dict[str, Any]]:
        domain_lower = domain.lower()
        results: list[dict[str, Any]] = []
        for skill in self.store.list_skills():
            if domain_lower in skill.domain.lower():
                results.append({
                    "skill_id": skill.skill_id,
                    "name": skill.name,
                    "domain": skill.domain,
                    "active_version": skill.active_version,
                })
            if len(results) >= limit:
                break
        return results

    def get_skill_summary(self, skill_id: str) -> dict[str, Any] | None:
        try:
            skill = self.store.get_skill(skill_id)
        except KeyError:
            return None
        version = skill.get_active_version()
        return {
            "skill_id": skill.skill_id,
            "name": skill.name,
            "domain": skill.domain,
            "active_version": version.version,
            "total_versions": len(skill.versions),
            "logic_type": version.static_logic.get("type", "unknown"),
            "required_fields": version.static_logic.get("required_fields", []),
            "domain_keywords": version.static_logic.get("domain_keywords", []),
            "quality_gate": version.quality_gate,
            "trigger_policy": {
                "on_error": version.trigger_policy.on_error,
                "on_quality_below_threshold": version.trigger_policy.on_quality_below_threshold,
                "force_dynamic": version.trigger_policy.force_dynamic,
            },
        }

    def get_all_skills_index(self) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for skill in self.store.list_skills():
            version = skill.get_active_version()
            results.append({
                "skill_id": skill.skill_id,
                "name": skill.name,
                "domain": skill.domain,
                "active_version": version.version,
                "total_versions": len(skill.versions),
                "logic_type": version.static_logic.get("type", "unknown"),
                "domain_keywords": version.static_logic.get("domain_keywords", []),
            })
        return results
