from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class ESkillConfig:
    """Centralized configuration for ESkill runtime."""

    # Storage
    store_path: str = os.getenv("ESKILL_STORE_PATH", "data/registry.json")
    
    # LLM (optional)
    llm_api_key: str | None = os.getenv("ESKILL_LLM_API_KEY")
    llm_model: str = os.getenv("ESKILL_LLM_MODEL", "gpt-4o-mini")
    llm_base_url: str | None = os.getenv("ESKILL_LLM_BASE_URL")
    
    # Quality thresholds
    default_min_length: int = int(os.getenv("ESKILL_MIN_LENGTH", "0"))
    default_min_score: float = float(os.getenv("ESKILL_MIN_SCORE", "0.0"))
    
    # Domain guard
    domain_guard_enabled: bool = os.getenv("ESKILL_DOMAIN_GUARD", "true").lower() != "false"
    
    # Logging
    log_level: str = os.getenv("ESKILL_LOG_LEVEL", "INFO")
    
    # Solidification
    auto_solidify: bool = os.getenv("ESKILL_AUTO_SOLIDIFY", "true").lower() != "false"
    max_versions: int = int(os.getenv("ESKILL_MAX_VERSIONS", "100"))
    
    # Retry/Timeout
    retry_count: int = int(os.getenv("ESKILL_RETRY_COUNT", "3"))
    timeout_seconds: float = float(os.getenv("ESKILL_TIMEOUT", "30.0"))
    
    @property
    def quality_gate(self) -> dict[str, float]:
        return {
            "min_length": self.default_min_length,
            "min_score": self.default_min_score,
        }
    
    def get_llm_config(self) -> dict[str, str | None]:
        return {
            "api_key": self.llm_api_key,
            "model": self.llm_model,
            "base_url": self.llm_base_url,
        }


def from_env() -> ESkillConfig:
    """Create config from environment variables."""
    return ESkillConfig()


def from_dict(data: dict[str, str]) -> ESkillConfig:
    """Create config from dictionary."""
    return ESkillConfig(**{k: v for k, v in data.items() if hasattr(ESkillConfig, k)})
