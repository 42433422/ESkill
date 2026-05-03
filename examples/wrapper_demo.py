from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from eskill.adapter import DictSkillAdapter, FunctionSkillAdapter
from eskill.store import JsonSkillStore
from eskill.wrapper import ESkillWrapper


def hello_world_func(input_data: dict) -> dict:
    name = input_data.get("name", "World")
    return {"message": f"Hello, {name}!", "type": "greeting"}


def main() -> None:
    store_path = Path(__file__).resolve().parent / "data" / "wrapper_demo.json"
    store_path.parent.mkdir(parents=True, exist_ok=True)
    if store_path.exists():
        store_path.unlink()

    store = JsonSkillStore(store_path)

    print("=" * 50)
    print("示例1：函数技能包装（无 LLM）")
    print("=" * 50)

    func_skill = FunctionSkillAdapter(
        func=hello_world_func,
        skill_id="hello-world",
        name="Hello World",
        domain="greeting",
        domain_keywords=["hello", "greeting"],
    )

    eskill = ESkillWrapper(
        skill=func_skill,
        store=store,
        quality_gate={"min_length": 1},
    )

    print("描述:", eskill.describe())
    result = eskill.execute({"name": "ESkill"})
    print("结果:", result)

    print("\n" + "=" * 50)
    print("示例2：模板技能包装（有质量要求）")
    print("=" * 50)

    template_skill = DictSkillAdapter(
        template="产品简介：${name}\n特点：${features}\n用途：${use_case}",
        skill_id="product-brief",
        name="Product Brief",
        domain="product description brief",
        output_var="brief",
        required_fields=["name"],
        domain_keywords=["product", "brief", "简介"],
    )

    eskill2 = ESkillWrapper(
        skill=template_skill,
        store=store,
        quality_gate={"min_length": 30, "required_keys": ["brief"]},
    )

    print("描述:", eskill2.describe())
    result2 = eskill2.execute({
        "name": "ESkill 原型产品",
        "features": "自修复、动态适配、版本管理",
        "use_case": "企业级技能自动化",
        "domain": "product brief example",
    })
    print("结果:", result2)

    print("\n" + "=" * 50)
    print("示例3：查看运行历史")
    print("=" * 50)

    history = eskill.get_run_history()
    print(f"hello-world 运行记录: {len(history)} 条")
    for run in history:
        print(f"  - 阶段: {run.get('stage')}, 输出: {str(run.get('output_data', {}))[:60]}...")

    print(f"\n数据存储: {store_path}")


if __name__ == "__main__":
    main()
