from __future__ import annotations

from pathlib import Path

from eskill import (
    AdaptivePolicyEngine,
    AuditTrail,
    CrystalLibrary,
    ESkill,
    ESkillPipeline,
    ESkillPipelineRunner,
    ESkillRuntime,
    JsonSkillStore,
    LayeredMemoryStore,
    PipelineNode,
    SkillCrystalizer,
    SkillHealthChecker,
    SkillPackageManager,
    SkillSuiteResult,
    SkillTestCase,
    SkillTestRunner,
    SkillTestSuite,
    SkillVersion,
    TriggerPolicy,
    ValidationCenter,
)


def make_writer_store(path: Path) -> JsonSkillStore:
    store = JsonSkillStore(path)
    store.save_skill(
        ESkill(
            skill_id="writer",
            name="Writer",
            domain="ESkill writing",
            active_version=1,
            versions=[
                SkillVersion(
                    version=1,
                    static_logic={
                        "type": "template_transform",
                        "template": "Hi ${topic}",
                        "dynamic_template": "Hi ${topic}. ${details}",
                        "required_fields": ["topic"],
                        "output_var": "text",
                        "domain_keywords": ["ESkill"],
                    },
                    trigger_policy=TriggerPolicy(),
                    quality_gate={"min_length": 20},
                )
            ],
        )
    )
    return store


def test_audit_events_policy_and_crystalization(tmp_path: Path) -> None:
    store = make_writer_store(tmp_path / "registry.json")
    runtime = ESkillRuntime(store)

    run = runtime.run("writer", {"topic": "ESkill", "details": "dynamic patch"})

    assert run.stage == "solidified"
    summary = AuditTrail(store).summarize("writer")
    assert summary["by_type"]["dynamic_triggered"] == 1
    assert summary["by_type"]["version_solidified"] == 1

    policy = AdaptivePolicyEngine(store).recommend_trigger_policy("writer")
    assert policy.on_quality_below_threshold is True

    crystals = SkillCrystalizer(store).crystalize_successes("writer")
    assert crystals
    assert CrystalLibrary(store).search("writer", {"details": "dynamic patch"})


def test_skill_test_runner_and_health_downgrade(tmp_path: Path) -> None:
    store = JsonSkillStore(tmp_path / "registry.json")
    store.save_skill(
        ESkill(
            skill_id="greeter",
            name="Greeter",
            domain="greeting",
            active_version=2,
            versions=[
                SkillVersion(
                    version=1,
                    static_logic={
                        "type": "template_transform",
                        "template": "ok ${name}",
                        "required_fields": ["name"],
                        "output_var": "text",
                    },
                ),
                SkillVersion(
                    version=2,
                    static_logic={
                        "type": "template_transform",
                        "template": "broken ${name}",
                        "required_fields": ["name"],
                        "output_var": "text",
                    },
                ),
            ],
        )
    )
    suite = SkillTestSuite(
        suite_id="greeter-regression",
        skill_id="greeter",
        cases=[
            SkillTestCase(
                case_id="basic",
                input_data={"name": "Ada"},
                expected_keys=["text"],
                contains_all=["ok Ada"],
                expected_stage="static",
            )
        ],
    )

    direct = SkillTestRunner(ESkillRuntime(store)).run_suite(suite)
    assert isinstance(direct, SkillSuiteResult)
    assert direct.passed is False

    report = SkillHealthChecker(store).run_suite(suite)
    assert report.passed is False
    assert report.downgraded_to_version == 1
    assert store.get_skill("greeter").active_version == 1


def test_memory_market_and_pipeline(tmp_path: Path) -> None:
    store = JsonSkillStore(tmp_path / "registry.json")
    store.save_skill(
        ESkill(
            skill_id="first",
            name="First",
            domain="pipeline",
            active_version=1,
            versions=[
                SkillVersion(
                    version=1,
                    static_logic={
                        "type": "template_transform",
                        "template": "hello ${name}",
                        "required_fields": ["name"],
                        "output_var": "greeting",
                    },
                )
            ],
        )
    )
    store.save_skill(
        ESkill(
            skill_id="second",
            name="Second",
            domain="pipeline",
            active_version=1,
            versions=[
                SkillVersion(
                    version=1,
                    static_logic={
                        "type": "template_transform",
                        "template": "final ${greeting}",
                        "required_fields": ["greeting"],
                        "output_var": "result",
                    },
                )
            ],
        )
    )

    memory = LayeredMemoryStore(store).remember(
        "L3", {"template": "repair with greeting"}, skill_id="first", tags=["repair"]
    )
    assert memory.layer == "L3"
    assert LayeredMemoryStore(store).search(skill_id="first", query="repair")

    pipeline = ESkillPipeline(
        pipeline_id="p1",
        nodes=[
            PipelineNode("a", "first", {"name": "name"}),
            PipelineNode("b", "second", {"greeting": "a.greeting"}),
        ],
    )
    pipeline_result = ESkillPipelineRunner(store).run(pipeline, {"name": "Ada"})
    assert pipeline_result.passed is True
    assert pipeline_result.node_outputs["b"]["result"] == "final hello Ada"

    suite = SkillTestSuite(
        suite_id="first-suite",
        skill_id="first",
        cases=[
            SkillTestCase(
                case_id="first-basic",
                input_data={"name": "Ada"},
                expected_keys=["greeting"],
                contains_all=["hello Ada"],
            )
        ],
    )
    package = SkillPackageManager(store).export("first", suite)
    assert ValidationCenter(store).validate_package(package) is True
