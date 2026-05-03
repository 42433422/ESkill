from __future__ import annotations

import pytest
from pathlib import Path
from eskill import (
    ESkill, SkillVersion, TriggerPolicy, ESkillRuntime, JsonSkillStore,
    SkillDiscovery, ESkillConfig, SkillNotFoundError, MissingRequiredFieldsError,
    DomainOutOfScopeError, QualityCheckFailedError, RetryPolicy, ResiliencePolicy,
    ESkillError, ESkillErrorCode,
)
from eskill.errors import (
    RollbackTriggeredError, LLMAPIError, StoreWriteError, ToolNotAllowedError,
)
from eskill.resilience import FallbackStrategy


@pytest.fixture
def tmp_store(tmp_path: Path) -> JsonSkillStore:
    return JsonSkillStore(tmp_path / "test.json")


def create_test_skill_for_store(
    store,
    skill_id: str = "test-skill",
    domain: str = "test",
    keywords: list[str] | None = None,
    required_fields: list[str] | None = None,
) -> ESkill:
    skill = ESkill(
        skill_id=skill_id,
        name=skill_id.title(),
        domain=domain,
        active_version=1,
        versions=[
            SkillVersion(
                version=1,
                static_logic={
                    "type": "template_transform",
                    "template": "Result: ${input}",
                    "required_fields": required_fields or ["input"],
                    "domain_keywords": keywords or [domain],
                },
                trigger_policy=TriggerPolicy(
                    on_error=True,
                    on_quality_below_threshold=True,
                    force_dynamic=False,
                ),
                quality_gate={"min_length": 0},
            )
        ],
    )
    store.save_skill(skill)
    return skill


# ==================== 错误码体系测试 ====================

class TestESkillError:
    def test_error_code_enum_values(self):
        assert ESkillErrorCode.SKILL_NOT_FOUND.value == "skill_not_found"
        assert ESkillErrorCode.QUALITY_CHECK_FAILED.value == "quality_check_failed"
        assert ESkillErrorCode.DOMAIN_OUT_OF_SCOPE.value == "domain_out_of_scope"
        assert ESkillErrorCode.LLM_API_ERROR.value == "llm_api_error"

    def test_eskill_error_to_dict(self):
        error = ESkillError(
            code=ESkillErrorCode.SKILL_NOT_FOUND,
            message="Skill not found",
            skill_id="test",
            version=1,
        )
        d = error.to_dict()
        assert d["code"] == "skill_not_found"
        assert d["skill_id"] == "test"
        assert d["version"] == 1

    def test_eskill_error_from_dict(self):
        data = {
            "code": "quality_check_failed",
            "message": "Quality check failed: min_length",
            "details": {"score": 0.5},
            "skill_id": "test",
            "version": 2,
        }
        error = ESkillError.from_dict(data)
        assert error.code == ESkillErrorCode.QUALITY_CHECK_FAILED
        assert error.skill_id == "test"

    def test_skill_not_found_error(self):
        error = SkillNotFoundError("missing-skill")
        assert error.code == ESkillErrorCode.SKILL_NOT_FOUND
        assert "missing-skill" in str(error)

    def test_missing_required_fields_error(self):
        error = MissingRequiredFieldsError(["name", "age"])
        assert error.code == ESkillErrorCode.MISSING_REQUIRED_FIELDS
        assert "name, age" in str(error)

    def test_domain_out_of_scope_error(self):
        error = DomainOutOfScopeError("test")
        assert error.code == ESkillErrorCode.DOMAIN_OUT_OF_SCOPE

    def test_quality_check_failed_error(self):
        error = QualityCheckFailedError(["min_length:5<10"], 0.5)
        assert error.code == ESkillErrorCode.QUALITY_CHECK_FAILED

    def test_rollback_triggered_error(self):
        error = RollbackTriggeredError("Test error", rolled_back=True)
        assert error.code == ESkillErrorCode.ROLLBACK_TRIGGERED

    def test_store_write_error(self):
        error = StoreWriteError("Disk full")
        assert error.code == ESkillErrorCode.STORE_WRITE_FAILED


# ==================== 异常处理测试 ====================

class TestRetryPolicy:
    def test_retry_on_transient_error(self):
        policy = RetryPolicy(max_retries=2, backoff_factor=0.01)
        call_count = 0

        def failing_func():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ESkillError(ESkillErrorCode.LLM_API_ERROR, "Transient error")
            return "success"

        result = policy.execute(failing_func)
        assert result == "success"
        assert call_count == 3

    def test_no_retry_on_permanent_error(self):
        policy = RetryPolicy(max_retries=3, backoff_factor=0.01)

        def permanent_error():
            raise ESkillError(ESkillErrorCode.SKILL_NOT_FOUND, "Not found")

        with pytest.raises(ESkillError) as exc_info:
            policy.execute(permanent_error)
        assert exc_info.value.code == ESkillErrorCode.SKILL_NOT_FOUND

    def test_max_retries_exceeded(self):
        policy = RetryPolicy(max_retries=2, backoff_factor=0.01)

        def always_fail():
            raise ESkillError(ESkillErrorCode.LLM_API_ERROR, "Always fails")

        with pytest.raises(ESkillError):
            policy.execute(always_fail)


class TestResiliencePolicy:
    def test_fallback_on_error(self):
        policy = ResiliencePolicy(
            fallback_func=lambda: "fallback_result"
        )

        def failing_func():
            raise ESkillError(ESkillErrorCode.LLM_API_ERROR, "API error")

        result = policy.execute(failing_func)
        assert result == "fallback_result"


# ==================== 配置测试 ====================

class TestESkillConfig:
    def test_default_values(self):
        config = ESkillConfig()
        assert config.log_level == "INFO"
        assert config.auto_solidify is True
        assert config.domain_guard_enabled is True
        assert config.retry_count == 3
        assert config.timeout_seconds == 30.0

    def test_quality_gate_property(self):
        config = ESkillConfig(default_min_length=10, default_min_score=0.5)
        assert config.quality_gate == {"min_length": 10, "min_score": 0.5}


# ==================== 技能发现测试 ====================

class TestSkillDiscovery:
    def test_search_by_name(self, tmp_store: JsonSkillStore):
        create_test_skill_for_store(tmp_store, "hello-world", domain="greeting")
        discovery = SkillDiscovery(tmp_store)
        results = discovery.search("hello")
        assert len(results) > 0
        assert results[0]["skill_id"] == "hello-world"

    def test_search_by_domain(self, tmp_store: JsonSkillStore):
        create_test_skill_for_store(tmp_store, "greeting-skill", domain="greeting")
        discovery = SkillDiscovery(tmp_store)
        results = discovery.search("greeting")
        assert len(results) > 0

    def test_search_returns_empty_for_no_match(self, tmp_store: JsonSkillStore):
        create_test_skill_for_store(tmp_store, "test-skill", domain="test")
        discovery = SkillDiscovery(tmp_store)
        results = discovery.search("nonexistent")
        assert len(results) == 0

    def test_list_by_domain(self, tmp_store: JsonSkillStore):
        create_test_skill_for_store(tmp_store, "greeting-1", domain="greeting")
        create_test_skill_for_store(tmp_store, "greeting-2", domain="greeting")
        create_test_skill_for_store(tmp_store, "other-skill", domain="other")
        discovery = SkillDiscovery(tmp_store)
        results = discovery.list_by_domain("greeting")
        assert len(results) == 2

    def test_get_skill_summary(self, tmp_store: JsonSkillStore):
        create_test_skill_for_store(tmp_store, "test-skill", domain="test")
        discovery = SkillDiscovery(tmp_store)
        summary = discovery.get_skill_summary("test-skill")
        assert summary is not None
        assert summary["skill_id"] == "test-skill"
        assert summary["active_version"] == 1

    def test_get_skill_summary_not_found(self, tmp_store: JsonSkillStore):
        discovery = SkillDiscovery(tmp_store)
        summary = discovery.get_skill_summary("nonexistent")
        assert summary is None

    def test_get_all_skills_index(self, tmp_store: JsonSkillStore):
        create_test_skill_for_store(tmp_store, "skill-1", domain="domain1")
        create_test_skill_for_store(tmp_store, "skill-2", domain="domain2")
        discovery = SkillDiscovery(tmp_store)
        index = discovery.get_all_skills_index()
        assert len(index) == 2

    def test_search_limit(self, tmp_store: JsonSkillStore):
        for i in range(5):
            create_test_skill_for_store(tmp_store, f"test-skill-{i}", domain="test")
        discovery = SkillDiscovery(tmp_store)
        results = discovery.search("test", limit=2)
        assert len(results) <= 2


# ==================== 版本 diff 测试 ====================

class TestVersionDiff:
    def test_diff_between_versions(self, tmp_store: JsonSkillStore):
        skill = ESkill(
            skill_id="test-diff",
            name="Test Diff",
            domain="test",
            active_version=2,
            versions=[
                SkillVersion(
                    version=1,
                    static_logic={"template": "v1", "type": "template_transform"},
                    trigger_policy=TriggerPolicy(),
                    quality_gate={},
                ),
                SkillVersion(
                    version=2,
                    static_logic={"template": "v2", "type": "template_transform", "new_field": "added"},
                    trigger_policy=TriggerPolicy(),
                    quality_gate={},
                ),
            ],
        )
        tmp_store.save_skill(skill)
        diff = tmp_store.get_version_diff("test-diff", 1, 2)
        assert "new_field" in diff["added"]
        assert diff["changed"]["template"]["old"] == "v1"
        assert diff["changed"]["template"]["new"] == "v2"

    def test_diff_no_previous_version(self, tmp_store: JsonSkillStore):
        skill = ESkill(
            skill_id="single-version",
            name="Single Version",
            domain="test",
            active_version=1,
            versions=[
                SkillVersion(
                    version=1,
                    static_logic={"template": "v1"},
                    trigger_policy=TriggerPolicy(),
                    quality_gate={},
                ),
            ],
        )
        tmp_store.save_skill(skill)
        diff = tmp_store.get_version_diff("single-version", 1)
        assert diff.get("message") == "No previous version"

    def test_diff_version_not_found(self, tmp_store: JsonSkillStore):
        skill = ESkill(
            skill_id="test-version-missing",
            name="Test",
            domain="test",
            active_version=1,
            versions=[
                SkillVersion(
                    version=1,
                    static_logic={},
                    trigger_policy=TriggerPolicy(),
                    quality_gate={},
                ),
            ],
        )
        tmp_store.save_skill(skill)
        with pytest.raises(ValueError):
            tmp_store.get_version_diff("test-version-missing", 999)


# ==================== 异步运行测试 ====================

class TestAsyncRuntime:
    @pytest.mark.asyncio
    async def test_async_run_basic(self, tmp_store: JsonSkillStore):
        from eskill import AsyncESkillRuntime
        create_test_skill_for_store(tmp_store, "async-test", domain="async")
        runtime = AsyncESkillRuntime(tmp_store)
        run = await runtime.run("async-test", {"input": "test"})
        assert run.stage in ["static", "dynamic", "solidified"]

    @pytest.mark.asyncio
    async def test_async_run_missing_required(self, tmp_store: JsonSkillStore):
        from eskill import AsyncESkillRuntime
        create_test_skill_for_store(tmp_store, "async-missing", domain="async", required_fields=["name"])
        runtime = AsyncESkillRuntime(tmp_store)
        with pytest.raises(MissingRequiredFieldsError):
            await runtime.run("async-missing", {"input": "test"})


# ==================== SQLite 存储测试 ====================

def create_sqlite_skill(store, skill_id: str, domain: str) -> ESkill:
    skill = ESkill(
        skill_id=skill_id,
        name=skill_id.title(),
        domain=domain,
        active_version=1,
        versions=[
            SkillVersion(
                version=1,
                static_logic={
                    "type": "template_transform",
                    "template": "Result: ${input}",
                    "required_fields": ["input"],
                    "domain_keywords": [domain],
                },
                trigger_policy=TriggerPolicy(
                    on_error=True,
                    on_quality_below_threshold=True,
                    force_dynamic=False,
                ),
                quality_gate={"min_length": 0},
            )
        ],
    )
    store.save_skill(skill)
    return skill


class TestSQLiteStore:
    def test_sqlite_store_basic(self, tmp_path: Path):
        from eskill import SQLiteSkillStore
        store = SQLiteSkillStore(tmp_path / "test.db")
        create_sqlite_skill(store, "sqlite-test", domain="sqlite")
        retrieved = store.get_skill("sqlite-test")
        assert retrieved.skill_id == "sqlite-test"

    def test_sqlite_store_has_skill(self, tmp_path: Path):
        from eskill import SQLiteSkillStore
        store = SQLiteSkillStore(tmp_path / "test.db")
        create_sqlite_skill(store, "sqlite-test", domain="sqlite")
        assert store.has_skill("sqlite-test") is True
        assert store.has_skill("nonexistent") is False

    def test_sqlite_store_list_skills(self, tmp_path: Path):
        from eskill import SQLiteSkillStore
        store = SQLiteSkillStore(tmp_path / "test.db")
        create_sqlite_skill(store, "sqlite-1", domain="sqlite")
        create_sqlite_skill(store, "sqlite-2", domain="sqlite")
        skills = store.list_skills()
        assert len(skills) == 2

    def test_sqlite_store_list_runs(self, tmp_path: Path):
        from eskill import SQLiteSkillStore, SkillRun
        store = SQLiteSkillStore(tmp_path / "test.db")
        create_sqlite_skill(store, "sqlite-runs", domain="sqlite")
        run = SkillRun(
            run_id="test-run",
            skill_id="sqlite-runs",
            stage="static",
            input_data={"input": "test"},
        )
        store.append_run(run)
        runs = store.list_runs("sqlite-runs")
        assert len(runs) == 1

    def test_sqlite_store_list_events(self, tmp_path: Path):
        from eskill import SQLiteSkillStore, EvolutionEvent
        store = SQLiteSkillStore(tmp_path / "test.db")
        create_sqlite_skill(store, "sqlite-events", domain="sqlite")
        event = EvolutionEvent(
            skill_id="sqlite-events",
            event_type="run_started",
            stage="static",
        )
        store.append_event(event)
        events = store.list_events("sqlite-events")
        assert len(events) == 1

    def test_sqlite_store_set_active_version(self, tmp_path: Path):
        from eskill import SQLiteSkillStore
        store = SQLiteSkillStore(tmp_path / "test.db")
        skill = ESkill(
            skill_id="sqlite-version",
            name="Version Test",
            domain="test",
            active_version=1,
            versions=[
                SkillVersion(version=1, static_logic={}, trigger_policy=TriggerPolicy(), quality_gate={}),
                SkillVersion(version=2, static_logic={}, trigger_policy=TriggerPolicy(), quality_gate={}),
            ],
        )
        store.save_skill(skill)
        updated = store.set_active_version("sqlite-version", 2)
        assert updated.active_version == 2
