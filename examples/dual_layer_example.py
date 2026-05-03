"""双层进化架构示例 —— Employee 层 + Skill 层协同自修复。

场景：销售助手 AI Employee
- Employee 层：perception/memory/cognition/actions 四层自修复
- Skill 层：工作流中的 greet/query/recommend 节点自修复
"""

from __future__ import annotations

import json
import tempfile

from eskill import (
    DictSkillAdapter,
    DualLayerOrchestrator,
    EmployeeLayerConfig,
    JsonSkillStore,
    SkillNodeConfig,
)


def main():
    # 创建临时存储
    store = JsonSkillStore(tempfile.mktemp(suffix=".json"))

    # 创建双层编排器
    orchestrator = DualLayerOrchestrator(store)

    # ========== 定义 Skill 节点执行函数 ==========

    def greet_fn(input_data):
        """问候节点 —— 模拟有时会失败的 Skill。"""
        name = input_data.get("name", "客人")
        # 模拟偶尔失败
        if name == "error_test":
            raise ValueError("问候服务暂时不可用")
        return {"greeting": f"您好，{name}！欢迎光临！", "type": "greeting"}

    def query_product_fn(input_data):
        """查询商品节点。"""
        query = input_data.get("query", "")
        if not query:
            return {"error": "缺少查询参数", "products": []}
        # 模拟 API 偶尔超时
        if query == "timeout_test":
            raise TimeoutError("商品服务超时")
        return {
            "products": [
                {"id": 1, "name": "iPhone 15", "price": 5999},
                {"id": 2, "name": "iPhone 15 Pro", "price": 7999},
            ],
            "query": query,
        }

    def recommend_fn(input_data):
        """推荐节点 —— 模拟质量不达标的场景。"""
        products = input_data.get("products", [])
        if not products:
            return {"error": "没有可推荐的商品", "recommendation": ""}
        # 模拟质量不达标（缺少 score 字段）
        if input_data.get("force_low_quality"):
            return {"recommendation": "随便推荐一个"}  # 缺少 score
        return {
            "recommendation": f"推荐您购买 {products[0]['name']}",
            "score": 0.95,
            "product_id": products[0]["id"],
        }

    # ========== 定义 Employee 层配置 ==========

    layer_config = EmployeeLayerConfig(
        perception_enabled=True,
        memory_enabled=True,
        cognition_enabled=True,
        actions_enabled=True,
        quality_gate={
            "perception": {"required_keys": ["normalized_input"]},
            "memory": {"required_keys": ["session"]},
            "cognition": {"required_keys": ["reasoning"]},
            "actions": {"required_keys": ["outputs"]},
        },
        trigger_policy={
            "on_error": True,
            "on_quality_below_threshold": True,
        },
    )

    # ========== 定义完整的双层员工 ==========

    emp_def = orchestrator.define_employee(
        employee_id="sales_assistant",
        layer_config=layer_config,
        skills=[
            {
                "node_id": "greet",
                "type": "eskill",
                "name": "问候客户",
                "execute": greet_fn,
                "quality_gate": {"required_keys": ["greeting"]},
                "retry_count": 1,
            },
            {
                "node_id": "query_product",
                "type": "openapi",
                "name": "查询商品",
                "execute": query_product_fn,
                "quality_gate": {"required_keys": ["products"]},
                "fallback_strategy": "default",
                "retry_count": 2,
            },
            {
                "node_id": "recommend",
                "type": "employee",
                "name": "智能推荐",
                "execute": recommend_fn,
                "quality_gate": {"required_keys": ["recommendation", "score"], "min_score": 0.8},
                "retry_count": 1,
            },
        ],
    )

    print("=" * 60)
    print("双层进化架构示例 —— 销售助手")
    print("=" * 60)
    print(f"\n员工定义: {json.dumps(emp_def, ensure_ascii=False, indent=2)}")

    # ========== 测试 1：正常执行 Skill 节点 ==========
    print("\n" + "=" * 60)
    print("测试 1：正常执行问候节点")
    print("=" * 60)

    result = orchestrator.run_skill_node(
        "greet",
        {"name": "张三"},
    )
    print(f"结果: {json.dumps(result, ensure_ascii=False, indent=2)}")

    # ========== 测试 2：Skill 节点失败自修复 ==========
    print("\n" + "=" * 60)
    print("测试 2：问候节点失败 → 自修复")
    print("=" * 60)

    result = orchestrator.run_skill_node(
        "greet",
        {"name": "error_test"},  # 触发失败
    )
    print(f"结果: {json.dumps(result, ensure_ascii=False, indent=2)}")

    # ========== 测试 3：质量不达标自修复 ==========
    print("\n" + "=" * 60)
    print("测试 3：推荐节点质量不达标 → 自修复")
    print("=" * 60)

    result = orchestrator.run_skill_node(
        "recommend",
        {"products": [{"id": 1, "name": "iPhone 15"}], "force_low_quality": True},
    )
    print(f"结果: {json.dumps(result, ensure_ascii=False, indent=2)}")

    # ========== 测试 4：Employee 层执行 ==========
    print("\n" + "=" * 60)
    print("测试 4：Employee 层完整执行")
    print("=" * 60)

    def my_perception(data):
        return {"normalized_input": data, "type": "text", "intent": "buy_phone"}

    def my_memory(data):
        return {"session": {"user_id": "u123"}, "long_term": None}

    def my_cognition(data):
        return {
            "reasoning": "用户想买手机，预算5000-8000，偏好苹果",
            "task": "推荐手机",
        }

    def my_actions(data):
        return {
            "handlers": ["recommend", "notify"],
            "outputs": [
                {"handler": "recommend", "product": "iPhone 15"},
                {"handler": "notify", "status": "ok"},
            ],
        }

    result = orchestrator.run(
        employee_id="sales_assistant",
        task="推荐一款手机",
        input_data={"budget": 6000, "brand": "apple"},
        perception_fn=my_perception,
        memory_fn=my_memory,
        cognition_fn=my_cognition,
        actions_fn=my_actions,
    )
    print(f"结果: {json.dumps(result, ensure_ascii=False, indent=2)}")

    # ========== 测试 5：Employee 层感知失败自修复 ==========
    print("\n" + "=" * 60)
    print("测试 5：Employee 感知层失败 → 自修复")
    print("=" * 60)

    def broken_perception(data):
        # 模拟解析失败
        return {"parse_error": "无法识别输入格式", "type": "unknown"}

    result = orchestrator.run(
        employee_id="sales_assistant",
        task="解析复杂输入",
        input_data={"raw": "some weird format"},
        perception_fn=broken_perception,
        memory_fn=my_memory,
        cognition_fn=my_cognition,
        actions_fn=my_actions,
    )
    print(f"结果: {json.dumps(result, ensure_ascii=False, indent=2)}")

    # ========== 双层健康报告 ==========
    print("\n" + "=" * 60)
    print("双层架构健康报告")
    print("=" * 60)

    report = orchestrator.get_report()
    print(f"报告: {json.dumps(report, ensure_ascii=False, indent=2)}")

    print("\n" + "=" * 60)
    print("示例完成！")
    print("=" * 60)


if __name__ == "__main__":
    main()
