from __future__ import annotations

from enum import Enum
from typing import Any


class ESkillErrorCode(Enum):
    """统一错误码，便于调用方处理。"""

    # 技能相关
    SKILL_NOT_FOUND = "skill_not_found"
    SKILL_VERSION_NOT_FOUND = "skill_version_not_found"
    VERSION_INVALID = "version_invalid"
    DUPLICATE_SKLL = "duplicate_skill"

    # 执行相关
    MISSING_REQUIRED_FIELDS = "missing_required_fields"
    UNSUPPORTED_LOGIC_TYPE = "unsupported_logic_type"
    PIPELINE_STEP_ERROR = "pipeline_step_error"
    TOOL_NOT_ALLOWED = "tool_not_allowed"

    # 质量门控
    QUALITY_CHECK_FAILED = "quality_check_failed"
    QUALITY_SCORE_TOO_LOW = "quality_score_too_low"
    MISSING_REQUIRED_KEYS = "missing_required_keys"
    MIN_LENGTH_NOT_MET = "min_length_not_met"

    # 动态适配
    DOMAIN_OUT_OF_SCOPE = "domain_out_of_scope"
    DYNAMIC_PATCH_FAILED = "dynamic_patch_failed"
    SANDBOX_VALIDATION_FAILED = "sandbox_validation_failed"
    ROLLBACK_TRIGGERED = "rollback_triggered"

    # 存储相关
    STORE_READ_FAILED = "store_read_failed"
    STORE_WRITE_FAILED = "store_write_failed"
    LOCK_TIMEOUT = "lock_timeout"

    # LLM 相关
    LLM_API_ERROR = "llm_api_error"
    LLM_TIMEOUT = "llm_timeout"
    LLM_RATE_LIMITED = "llm_rate_limited"
    LLM_INVALID_RESPONSE = "llm_invalid_response"

    # 配置相关
    CONFIG_INVALID = "config_invalid"
    CONFIG_MISSING = "config_missing"


class ESkillError(Exception):
    """统一错误类。"""

    def __init__(
        self,
        code: ESkillErrorCode,
        message: str,
        details: dict[str, Any] | None = None,
        skill_id: str | None = None,
        run_id: str | None = None,
        version: int | None = None,
    ):
        self.code = code
        self.message = message
        self.details = details or {}
        self.skill_id = skill_id
        self.run_id = run_id
        self.version = version
        super().__init__(f"[{code.value}] {message}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code.value,
            "message": self.message,
            "details": self.details,
            "skill_id": self.skill_id,
            "run_id": self.run_id,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ESkillError:
        return cls(
            code=ESkillErrorCode(data["code"]),
            message=data["message"],
            details=data.get("details"),
            skill_id=data.get("skill_id"),
            run_id=data.get("run_id"),
            version=data.get("version"),
        )


class SkillNotFoundError(ESkillError):
    def __init__(self, skill_id: str):
        super().__init__(
            ESkillErrorCode.SKILL_NOT_FOUND,
            f"Skill not found: {skill_id}",
            skill_id=skill_id,
        )


class MissingRequiredFieldsError(ESkillError):
    def __init__(self, missing_fields: list[str]):
        super().__init__(
            ESkillErrorCode.MISSING_REQUIRED_FIELDS,
            f"Missing required fields: {', '.join(missing_fields)}",
            details={"missing_fields": missing_fields},
        )


class DomainOutOfScopeError(ESkillError):
    def __init__(self, skill_id: str):
        super().__init__(
            ESkillErrorCode.DOMAIN_OUT_OF_SCOPE,
            "Dynamic phase rejected: input is outside skill domain",
            skill_id=skill_id,
        )


class QualityCheckFailedError(ESkillError):
    def __init__(self, issues: list[str], score: float):
        super().__init__(
            ESkillErrorCode.QUALITY_CHECK_FAILED,
            f"Quality check failed: {', '.join(issues)}",
            details={"issues": issues, "score": score},
        )


class ToolNotAllowedError(ESkillError):
    def __init__(self, tool: str):
        super().__init__(
            ESkillErrorCode.TOOL_NOT_ALLOWED,
            f"Tool is not in ESkill allowlist: {tool}",
            details={"tool": tool},
        )


class RollbackTriggeredError(ESkillError):
    def __init__(self, error: str, rolled_back: bool = True):
        super().__init__(
            ESkillErrorCode.ROLLBACK_TRIGGERED,
            error,
            details={"rolled_back": rolled_back},
        )


class LLMAPIError(ESkillError):
    def __init__(self, error: str, details: dict[str, Any] | None = None):
        super().__init__(
            ESkillErrorCode.LLM_API_ERROR,
            error,
            details=details,
        )


class StoreWriteError(ESkillError):
    def __init__(self, error: str):
        super().__init__(
            ESkillErrorCode.STORE_WRITE_FAILED,
            f"Store write failed: {error}",
            details={"error": error},
        )
