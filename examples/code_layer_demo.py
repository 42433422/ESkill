"""Demo: code-layer self-healing (price calculator) + optional events on JsonSkillStore."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from eskill import (  # noqa: E402
    CodeFunctionSignature,
    CodeSkill,
    CodeSkillRuntime,
    CodeSkillVersion,
    CodeTestCase,
    JsonCodeSkillStore,
    JsonSkillStore,
)

DATA = ROOT / "data" / "code_layer_demo.json"
EVENTS = ROOT / "data" / "code_layer_events.json"


def main() -> None:
    DATA.parent.mkdir(parents=True, exist_ok=True)
    code_store = JsonCodeSkillStore(DATA)
    event_store = JsonSkillStore(EVENTS)

    source = """
def calculate_price(items, discount=0.0):
    total = sum(item["price"] * item["quantity"] for item in items)
    return {"total": total * (1 - discount), "discount": discount}
"""
    ver = CodeSkillVersion(
        version=1,
        source_code=source,
        function_name="calculate_price",
        signature=CodeFunctionSignature(
            params=["items", "discount"],
            return_type="dict",
            required_params=["items"],
        ),
        dependencies=[],
        test_cases=[
            CodeTestCase(
                case_id="basic",
                input_data={"items": [{"price": 100, "quantity": 2}], "discount": 0.1},
                expected_output={"total": 180.0, "discount": 0.1},
            ),
            CodeTestCase(
                case_id="none_price",
                input_data={"items": [{"price": None, "quantity": 1}], "discount": 0.0},
                expected_output=None,
            ),
        ],
        quality_gate={"required_keys": ["total", "discount"]},
        domain_keywords=[],
    )
    skill = CodeSkill(
        skill_id="price-calculator",
        name="Price calculator",
        domain="null",
        active_version=1,
        versions=[ver],
    )
    code_store.save_code_skill(skill)

    rt = CodeSkillRuntime(code_store, event_store=event_store)
    run = rt.run(
        "price-calculator",
        {"items": [{"price": None, "quantity": 1}], "discount": 0.0},
    )
    print("stage:", run.stage)
    print("active_version:", code_store.get_code_skill("price-calculator").active_version)
    print("sample_events:", len(event_store.list_events("price-calculator")))


if __name__ == "__main__":
    main()
