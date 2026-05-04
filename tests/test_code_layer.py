from __future__ import annotations

from pathlib import Path

from eskill import (
    CodeDiagnosis,
    CodeFunctionSignature,
    CodeSandbox,
    CodeSkill,
    CodeSkillRuntime,
    CodeSkillVersion,
    CodeTestCase,
    CodeValidator,
    ESkill,
    ESkillRuntime,
    HybridSkillRuntime,
    JsonCodeSkillStore,
    JsonSkillStore,
    RuleBasedCodePatchGenerator,
    SkillVersion,
    TriggerPolicy,
)


def _diag(error_type: str, message: str = "") -> CodeDiagnosis:
    fix_map = {
        "KeyError": "missing_key",
        "TypeError": "type_mismatch",
        "ValueError": "invalid_value",
        "AttributeError": "missing_attribute",
        "IndexError": "index_out_of_range",
        "TimeoutError": "timeout",
        "ZeroDivisionError": "invalid_value",
    }
    return CodeDiagnosis(
        error_type=error_type,
        error_message=message,
        traceback_str="",
        failing_line="",
        local_variables={},
        suggested_fix_type=fix_map.get(error_type, "unknown"),
    )


def _sig(*params: str) -> CodeFunctionSignature:
    return CodeFunctionSignature(
        params=list(params),
        return_type="dict",
        required_params=list(params[:1]) if params else [],
    )


def test_validator_rejects_eval() -> None:
    v = CodeValidator()
    src = "def f(x):\n    return eval(x)\n"
    r = v.validate(
        src,
        function_name="f",
        signature=CodeFunctionSignature(params=["x"], return_type="any", required_params=["x"]),
    )
    assert r.safe is False
    assert any("forbidden" in i for i in r.issues)


def test_sandbox_executes() -> None:
    sb = CodeSandbox(timeout_seconds=5.0)
    src = "def add(a, b=0):\n    return {'sum': a + b}\n"
    r = sb.execute(src, "add", {"a": 2, "b": 3})
    assert r.success
    assert r.output == {"sum": 5}


def test_sandbox_timeout() -> None:
    sb = CodeSandbox(timeout_seconds=0.1)
    src = "import time\ndef slow():\n    time.sleep(2)\n    return {}\n"
    r = sb.execute(src, "slow", {})
    assert r.success is False
    assert "time" in (r.error_type or r.error_message).lower() or "timeout" in r.error_message.lower()


def test_code_runtime_solidifies_on_type_mismatch(tmp_path: Path) -> None:
    reg = tmp_path / "code.json"
    store = JsonCodeSkillStore(reg)
    ev = tmp_path / "ev.json"
    jstore = JsonSkillStore(ev)

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
        name="Price",
        domain="null",
        active_version=1,
        versions=[ver],
    )
    store.save_code_skill(skill)

    rt = CodeSkillRuntime(store, event_store=jstore)
    run = rt.run(
        "price-calculator",
        {"items": [{"price": None, "quantity": 1}], "discount": 0.0},
    )
    assert run.stage == "solidified"
    assert store.get_code_skill("price-calculator").active_version == 2


def test_hybrid_config_error_then_code(tmp_path: Path) -> None:
    cfg_path = tmp_path / "cfg.json"
    code_path = tmp_path / "code.json"
    cfg_store = JsonSkillStore(cfg_path)
    code_store = JsonCodeSkillStore(code_path)

    cfg_store.save_skill(
        ESkill(
            skill_id="hybrid-skill",
            name="H",
            domain="demo",
            active_version=1,
            skill_type="hybrid",
            versions=[
                SkillVersion(
                    version=1,
                    static_logic={
                        "type": "template_transform",
                        "template": "x",
                        "required_fields": ["missing_required_xyz"],
                        "output_var": "out",
                    },
                    trigger_policy=TriggerPolicy(on_error=False),
                )
            ],
        )
    )

    src = "def run(x=0):\n    return {'out': 42}\n"
    code_store.save_code_skill(
        CodeSkill(
            skill_id="hybrid-skill",
            name="H",
            domain="",
            active_version=1,
            versions=[
                CodeSkillVersion(
                    version=1,
                    source_code=src,
                    function_name="run",
                    signature=CodeFunctionSignature(
                        params=["x"], return_type="dict", required_params=[]
                    ),
                    test_cases=[],
                    quality_gate={"required_keys": ["out"]},
                    domain_keywords=[],
                )
            ],
        )
    )

    cfg_rt = ESkillRuntime(cfg_store)
    code_rt = CodeSkillRuntime(code_store)
    hybrid = HybridSkillRuntime(cfg_rt, code_rt, config_store=cfg_store)
    out = hybrid.run("hybrid-skill", {"x": 0})
    assert out.output_data.get("out") == 42
    assert out.stage == "static"


def test_validator_rejects_open_and_attribute_call() -> None:
    v = CodeValidator()
    src = "def f(x):\n    open('/etc/passwd')\n    return {}\n"
    r = v.validate(src, function_name="f", signature=_sig("x"))
    assert r.safe is False
    assert any("forbidden" in i for i in r.issues)

    src2 = "import os\ndef f(x):\n    os.system('echo')\n    return {}\n"
    r2 = v.validate(src2, function_name="f", signature=_sig("x"))
    assert r2.safe is False
    assert any("disallowed_import" in i or "disallowed_attribute_call" in i for i in r2.issues)


def test_validator_rejects_signature_drift() -> None:
    v = CodeValidator()
    src = "def f(y):\n    return {'y': y}\n"
    r = v.validate(src, function_name="f", signature=_sig("x"))
    assert r.safe is False
    assert any("signature_mismatch_param" in i or "missing_param" in i for i in r.issues)


def test_sandbox_blocks_eval_at_runtime() -> None:
    sb = CodeSandbox(timeout_seconds=5.0)
    src = "def f():\n    return {'r': eval('1+1')}\n"
    r = sb.execute(src, "f", {})
    assert r.success is False
    assert r.error_type == "NameError"


def test_sandbox_blocks_open_at_runtime() -> None:
    sb = CodeSandbox(timeout_seconds=5.0)
    src = "def f():\n    open('/etc/passwd')\n    return {}\n"
    r = sb.execute(src, "f", {})
    assert r.success is False
    assert r.error_type == "NameError"


def test_sandbox_blocks_disallowed_import_at_runtime() -> None:
    sb = CodeSandbox(timeout_seconds=5.0)
    src = "def f():\n    import os\n    return {'r': os.getcwd()}\n"
    r = sb.execute(src, "f", {})
    assert r.success is False
    assert r.error_type == "ImportError"


def test_sandbox_allows_whitelisted_import() -> None:
    sb = CodeSandbox(timeout_seconds=5.0)
    src = "def f():\n    import json\n    return {'r': json.dumps({'a': 1})}\n"
    r = sb.execute(src, "f", {})
    assert r.success is True
    assert r.output == {"r": '{"a": 1}'}


def test_rule_patch_generic_keyerror() -> None:
    src = (
        "def fetch(record):\n"
        "    name = record['name']\n"
        "    return {'n': name}\n"
    )
    gen = RuleBasedCodePatchGenerator()
    patch = gen.generate(
        src, "fetch", _sig("record"), _diag("KeyError", "'name'"), []
    )
    assert patch is not None
    assert "record.get('name')" in patch.patched_code or "record.get(\"name\")" in patch.patched_code

    sb = CodeSandbox(timeout_seconds=5.0)
    ok = sb.execute(patch.patched_code, "fetch", {"record": {}})
    assert ok.success is True


def test_rule_patch_generic_indexerror() -> None:
    src = "def first(xs):\n    return {'v': xs[0]}\n"
    gen = RuleBasedCodePatchGenerator()
    patch = gen.generate(src, "first", _sig("xs"), _diag("IndexError"), [])
    assert patch is not None
    sb = CodeSandbox(timeout_seconds=5.0)
    ok = sb.execute(patch.patched_code, "first", {"xs": []})
    assert ok.success is True
    assert ok.output == {"v": None}


def test_rule_patch_generic_typeerror_numeric() -> None:
    src = (
        "def total(xs):\n"
        "    s = 0\n"
        "    for x in xs:\n"
        "        s = s + x\n"
        "    return {'s': s}\n"
    )
    gen = RuleBasedCodePatchGenerator()
    patch = gen.generate(
        src,
        "total",
        _sig("xs"),
        _diag("TypeError", "unsupported operand type(s) for +: 'int' and 'NoneType'"),
        [],
    )
    assert patch is not None
    sb = CodeSandbox(timeout_seconds=5.0)
    ok = sb.execute(patch.patched_code, "total", {"xs": [1, 2, None, 3]})
    assert ok.success is True
    assert ok.output == {"s": 6}


def test_rule_patch_generic_attributeerror() -> None:
    src = "def show(o):\n    return {'n': o.name}\n"
    gen = RuleBasedCodePatchGenerator()
    patch = gen.generate(
        src,
        "show",
        _sig("o"),
        _diag("AttributeError", "'NoneType' object has no attribute 'name'"),
        [],
    )
    assert patch is not None
    sb = CodeSandbox(timeout_seconds=5.0)
    ok = sb.execute(patch.patched_code, "show", {"o": None})
    assert ok.success is True
    assert ok.output == {"n": None}


def test_rule_patch_generic_zero_division() -> None:
    src = "def div(a, b):\n    return {'q': a / b}\n"
    gen = RuleBasedCodePatchGenerator()
    patch = gen.generate(
        src,
        "div",
        _sig("a", "b"),
        _diag("ZeroDivisionError"),
        [],
    )
    assert patch is not None
    sb = CodeSandbox(timeout_seconds=5.0)
    ok = sb.execute(patch.patched_code, "div", {"a": 4, "b": 0})
    assert ok.success is True
    assert ok.output == {"q": 0}


def test_rule_patch_validator_accepts_outputs() -> None:
    """Every generic patch must still pass the validator (no forbidden calls/imports)."""
    v = CodeValidator()
    gen = RuleBasedCodePatchGenerator()

    cases = [
        (
            "def f(d):\n    return {'k': d['k']}\n",
            "f",
            _sig("d"),
            _diag("KeyError", "'k'"),
        ),
        (
            "def f(xs):\n    return {'v': xs[0]}\n",
            "f",
            _sig("xs"),
            _diag("IndexError"),
        ),
        (
            "def f(o):\n    return {'n': o.name}\n",
            "f",
            _sig("o"),
            _diag("AttributeError", "'NoneType' object has no attribute 'name'"),
        ),
        (
            "def f(a, b):\n    return {'q': a / b}\n",
            "f",
            _sig("a", "b"),
            _diag("ZeroDivisionError"),
        ),
        (
            "def f(xs):\n    s = 0\n    for x in xs:\n        s = s + x\n    return {'s': s}\n",
            "f",
            _sig("xs"),
            _diag("TypeError", "unsupported operand type(s) for +: 'int' and 'NoneType'"),
        ),
    ]
    for src, fn, sig, diag in cases:
        patch = gen.generate(src, fn, sig, diag, [])
        assert patch is not None, f"no patch for {diag.error_type}"
        report = v.validate(patch.patched_code, function_name=fn, signature=sig)
        assert report.safe, f"validator rejected {diag.error_type}: {report.issues}"


