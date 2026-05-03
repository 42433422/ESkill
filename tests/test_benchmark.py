"""性能基准测试"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from eskill import ESkill, ESkillRuntime, JsonSkillStore, SkillVersion, TriggerPolicy


def create_test_skill(store: JsonSkillStore, skill_id: str = "bench-test") -> ESkill:
    skill = ESkill(
        skill_id=skill_id,
        name="Benchmark Test",
        domain="benchmark",
        active_version=1,
        versions=[
            SkillVersion(
                version=1,
                static_logic={
                    "type": "template_transform",
                    "template": "Result: ${input}",
                    "required_fields": ["input"],
                    "domain_keywords": ["benchmark"],
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


def benchmark_store_operations(tmp_path: Path, iterations: int = 1000) -> dict[str, float]:
    """测试存储操作的基准性能"""
    store = JsonSkillStore(tmp_path / "bench.json")
    skill = create_test_skill(store, "bench-store")

    start = time.perf_counter()
    for i in range(iterations):
        store.get_skill("bench-store")
    read_time = time.perf_counter() - start

    start = time.perf_counter()
    for i in range(iterations):
        store.save_skill(skill)
    write_time = time.perf_counter() - start

    start = time.perf_counter()
    for i in range(iterations):
        store.list_skills()
    list_time = time.perf_counter() - start

    return {
        "read_ops_per_sec": round(iterations / read_time, 2),
        "write_ops_per_sec": round(iterations / write_time, 2),
        "list_ops_per_sec": round(iterations / list_time, 2),
        "read_latency_ms": round((read_time / iterations) * 1000, 4),
        "write_latency_ms": round((write_time / iterations) * 1000, 4),
        "list_latency_ms": round((list_time / iterations) * 1000, 4),
    }


def benchmark_runtime_execution(tmp_path: Path, iterations: int = 1000) -> dict[str, float]:
    """测试运行时执行的基准性能"""
    store = JsonSkillStore(tmp_path / "bench_runtime.json")
    create_test_skill(store, "bench-runtime")
    runtime = ESkillRuntime(store)

    input_data = {"input": "test"}

    start = time.perf_counter()
    for i in range(iterations):
        runtime.run("bench-runtime", input_data)
    total_time = time.perf_counter() - start

    return {
        "executions_per_sec": round(iterations / total_time, 2),
        "execution_latency_ms": round((total_time / iterations) * 1000, 4),
    }


def benchmark_wrapper_execution(tmp_path: Path, iterations: int = 1000) -> dict[str, float]:
    """测试 Wrapper 封装层的基准性能"""
    from eskill import DictSkillAdapter, ESkillWrapper

    store = JsonSkillStore(tmp_path / "bench_wrapper.json")

    skill = DictSkillAdapter(
        template="Hello, ${name}!",
        skill_id="bench-wrapper",
        name="Benchmark Wrapper",
        domain="hello",
        output_var="greeting",
        domain_keywords=["hello"],
    )

    eskill = ESkillWrapper(
        skill=skill,
        store=store,
        quality_gate={"min_length": 0},
    )

    input_data = {"name": "World"}

    start = time.perf_counter()
    for i in range(iterations):
        eskill.execute(input_data)
    total_time = time.perf_counter() - start

    return {
        "executions_per_sec": round(iterations / total_time, 2),
        "execution_latency_ms": round((total_time / iterations) * 1000, 4),
    }


def test_benchmark_store_operations(tmp_path: Path):
    results = benchmark_store_operations(tmp_path, 500)
    assert results["read_ops_per_sec"] > 100, f"Read OPS too low: {results['read_ops_per_sec']}"
    assert results["write_ops_per_sec"] > 100, f"Write OPS too low: {results['write_ops_per_sec']}"
    print("\n=== Store Benchmark ===")
    for k, v in results.items():
        print(f"  {k}: {v}")


def test_benchmark_runtime_execution(tmp_path: Path):
    results = benchmark_runtime_execution(tmp_path, 500)
    assert results["executions_per_sec"] > 100, f"Execution OPS too low: {results['executions_per_sec']}"
    print("\n=== Runtime Benchmark ===")
    for k, v in results.items():
        print(f"  {k}: {v}")


def test_benchmark_wrapper_execution(tmp_path: Path):
    results = benchmark_wrapper_execution(tmp_path, 500)
    assert results["executions_per_sec"] > 100, f"Wrapper OPS too low: {results['executions_per_sec']}"
    print("\n=== Wrapper Benchmark ===")
    for k, v in results.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        print("Running Store Benchmark...")
        store_results = benchmark_store_operations(tmp_path, 1000)
        print("\nStore Results:")
        for k, v in store_results.items():
            print(f"  {k}: {v}")

        print("\nRunning Runtime Benchmark...")
        runtime_results = benchmark_runtime_execution(tmp_path, 1000)
        print("\nRuntime Results:")
        for k, v in runtime_results.items():
            print(f"  {k}: {v}")

        print("\nRunning Wrapper Benchmark...")
        wrapper_results = benchmark_wrapper_execution(tmp_path, 1000)
        print("\nWrapper Results:")
        for k, v in wrapper_results.items():
            print(f"  {k}: {v}")
