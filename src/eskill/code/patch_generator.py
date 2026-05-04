"""Patch generation: rule-based and optional LLM."""

from __future__ import annotations

import ast
import json
import re
from abc import ABC, abstractmethod
from typing import Any

from .models import CodeDiagnosis, CodeFunctionSignature, CodePatch, CodeTestCase


class CodePatchGenerator(ABC):
    @abstractmethod
    def generate(
        self,
        source_code: str,
        function_name: str,
        signature: CodeFunctionSignature,
        diagnosis: CodeDiagnosis,
        test_cases: list[CodeTestCase],
        history: list[CodePatch] | None = None,
    ) -> CodePatch | None:
        ...


_KEY_FROM_MSG = re.compile(r"^['\"]?(?P<key>[^'\"]+)['\"]?$")
_ATTR_FROM_MSG = re.compile(
    r"has no attribute ['\"](?P<attr>[^'\"]+)['\"]"
)


def _extract_key_from_keyerror(message: str) -> str | None:
    msg = (message or "").strip()
    if not msg:
        return None
    if msg.startswith("'") and msg.endswith("'") and len(msg) >= 2:
        return msg[1:-1]
    if msg.startswith('"') and msg.endswith('"') and len(msg) >= 2:
        return msg[1:-1]
    m = _KEY_FROM_MSG.match(msg)
    return m.group("key") if m else msg


def _extract_attr_from_attributeerror(message: str) -> str | None:
    m = _ATTR_FROM_MSG.search(message or "")
    return m.group("attr") if m else None


def _safe_unparse(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except Exception:
        return ""


class _RewriteSubscriptToGet(ast.NodeTransformer):
    """Rewrite `obj[key]` -> `(obj.get(key) if isinstance(obj, dict) else obj[key])`.

    If `target_key` is provided, only rewrite when the literal subscript matches it.
    Idempotent: skip nodes already wrapped with `.get(...)` form.
    """

    def __init__(self, target_key: str | None = None) -> None:
        super().__init__()
        self.target_key = target_key
        self.changed = False

    def visit_Subscript(self, node: ast.Subscript) -> ast.AST:
        self.generic_visit(node)
        if not isinstance(node.ctx, ast.Load):
            return node
        slice_node = node.slice
        key_repr: str | None = None
        if isinstance(slice_node, ast.Constant) and isinstance(slice_node.value, str):
            key_repr = slice_node.value
        if self.target_key is not None and key_repr != self.target_key:
            return node
        # Build: obj.get(<slice>) if isinstance(obj, dict) else <original>
        obj = node.value
        get_call = ast.Call(
            func=ast.Attribute(value=obj, attr="get", ctx=ast.Load()),
            args=[slice_node],
            keywords=[],
        )
        guard = ast.Call(
            func=ast.Name(id="isinstance", ctx=ast.Load()),
            args=[obj, ast.Name(id="dict", ctx=ast.Load())],
            keywords=[],
        )
        replacement = ast.IfExp(test=guard, body=get_call, orelse=node)
        self.changed = True
        return ast.copy_location(replacement, node)


class _RewriteIndexToGuarded(ast.NodeTransformer):
    """Rewrite numeric `seq[i]` -> `(seq[i] if -len(seq) <= i < len(seq) else None)`."""

    def __init__(self) -> None:
        super().__init__()
        self.changed = False

    def visit_Subscript(self, node: ast.Subscript) -> ast.AST:
        self.generic_visit(node)
        if not isinstance(node.ctx, ast.Load):
            return node
        idx = node.slice
        # Only transform integer-literal indices to keep semantics safe
        if not (isinstance(idx, ast.Constant) and isinstance(idx.value, int)):
            return node
        seq = node.value
        # Only rewrite when seq is a simple Name to avoid duplicating side effects.
        if not isinstance(seq, ast.Name):
            return node
        len_call = ast.Call(
            func=ast.Name(id="len", ctx=ast.Load()),
            args=[seq],
            keywords=[],
        )
        neg_len = ast.UnaryOp(op=ast.USub(), operand=len_call)
        # Re-evaluate len() safely with try/None fallback would be ideal but
        # IfExp with comparison is sufficient for the common case.
        cmp = ast.Compare(
            left=neg_len,
            ops=[ast.LtE(), ast.Lt()],
            comparators=[idx, len_call],
        )
        replacement = ast.IfExp(
            test=cmp,
            body=node,
            orelse=ast.Constant(value=None),
        )
        self.changed = True
        return ast.copy_location(replacement, node)


class _RewriteAttrToGetattr(ast.NodeTransformer):
    """Rewrite `obj.<attr>` -> `getattr(obj, '<attr>', None)` for the failing attr.

    NOTE: ``getattr`` is in the validator's forbidden list. We instead rewrite to
    ``(obj.<attr> if hasattr(obj, '<attr>') else None)`` so the patched code
    still passes validation.
    """

    def __init__(self, attr_name: str) -> None:
        super().__init__()
        self.attr_name = attr_name
        self.changed = False

    def visit_Attribute(self, node: ast.Attribute) -> ast.AST:
        self.generic_visit(node)
        if node.attr != self.attr_name or not isinstance(node.ctx, ast.Load):
            return node
        guard = ast.Call(
            func=ast.Name(id="hasattr", ctx=ast.Load()),
            args=[node.value, ast.Constant(value=self.attr_name)],
            keywords=[],
        )
        replacement = ast.IfExp(test=guard, body=node, orelse=ast.Constant(value=None))
        self.changed = True
        return ast.copy_location(replacement, node)


class _RewriteDivToSafe(ast.NodeTransformer):
    """Rewrite `a / b` -> `(a / b if b not in (0, 0.0, None) else 0)`."""

    def __init__(self) -> None:
        super().__init__()
        self.changed = False

    def _visit_div(self, node: ast.BinOp) -> ast.AST:
        self.generic_visit(node)
        if not isinstance(node.op, (ast.Div, ast.FloorDiv, ast.Mod)):
            return node
        right = node.right
        guard = ast.Compare(
            left=right,
            ops=[ast.NotIn()],
            comparators=[
                ast.Tuple(
                    elts=[
                        ast.Constant(value=0),
                        ast.Constant(value=0.0),
                        ast.Constant(value=None),
                    ],
                    ctx=ast.Load(),
                )
            ],
        )
        replacement = ast.IfExp(test=guard, body=node, orelse=ast.Constant(value=0))
        self.changed = True
        return ast.copy_location(replacement, node)

    def visit_BinOp(self, node: ast.BinOp) -> ast.AST:
        return self._visit_div(node)


_NUMERIC_BINOPS = (
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.FloorDiv,
    ast.Mod,
    ast.Pow,
)


def _coerce_numeric(node: ast.expr) -> ast.expr:
    """Wrap ``node`` as ``(0 if node is None else node)`` (idempotent)."""
    # Idempotency: don't re-wrap an existing IfExp(... is None ...)
    if (
        isinstance(node, ast.IfExp)
        and isinstance(node.test, ast.Compare)
        and len(node.test.ops) == 1
        and isinstance(node.test.ops[0], ast.Is)
        and isinstance(node.test.comparators[0], ast.Constant)
        and node.test.comparators[0].value is None
    ):
        return node
    # Skip literal numeric constants — already concrete.
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float, complex)):
        return node
    test = ast.Compare(
        left=node, ops=[ast.Is()], comparators=[ast.Constant(value=None)]
    )
    return ast.IfExp(
        test=test,
        body=ast.Constant(value=0),
        orelse=node,
    )


class _CoerceNumericBinOps(ast.NodeTransformer):
    """Wrap numeric BinOp operands with ``(0 if operand is None else operand)``.

    Generic across functions; addresses the common ``TypeError: unsupported
    operand type(s) for *: 'NoneType' and ...`` failure mode without needing
    bespoke per-function rewrites.
    """

    def __init__(self) -> None:
        super().__init__()
        self.changed = False

    def visit_BinOp(self, node: ast.BinOp) -> ast.AST:
        self.generic_visit(node)
        if not isinstance(node.op, _NUMERIC_BINOPS):
            return node
        new_left = _coerce_numeric(node.left)
        new_right = _coerce_numeric(node.right)
        if new_left is node.left and new_right is node.right:
            return node
        self.changed = True
        return ast.copy_location(
            ast.BinOp(left=new_left, op=node.op, right=new_right), node
        )

    def visit_AugAssign(self, node: ast.AugAssign) -> ast.AST:
        self.generic_visit(node)
        if not isinstance(node.op, _NUMERIC_BINOPS):
            return node
        new_value = _coerce_numeric(node.value)
        if new_value is node.value:
            return node
        self.changed = True
        return ast.copy_location(
            ast.AugAssign(target=node.target, op=node.op, value=new_value), node
        )


def _apply_transformer(source_code: str, transformer: ast.NodeTransformer) -> str | None:
    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return None
    new_tree = transformer.visit(tree)
    ast.fix_missing_locations(new_tree)
    if not getattr(transformer, "changed", False):
        return None
    try:
        return ast.unparse(new_tree)
    except Exception:
        return None


class RuleBasedCodePatchGenerator(CodePatchGenerator):
    """Generic AST-based repairs keyed on diagnosis classification."""

    def generate(
        self,
        source_code: str,
        function_name: str,
        signature: CodeFunctionSignature,
        diagnosis: CodeDiagnosis,
        test_cases: list[CodeTestCase],
        history: list[CodePatch] | None = None,
    ) -> CodePatch | None:
        _ = test_cases, history
        fix_type = diagnosis.suggested_fix_type
        message = diagnosis.error_message or ""

        if fix_type == "missing_key":
            target = _extract_key_from_keyerror(message)
            patched = _apply_transformer(source_code, _RewriteSubscriptToGet(target_key=target))
            if patched and patched != source_code:
                return CodePatch(
                    reason=f"rule:missing_key:{target or 'any'}",
                    original_code=source_code,
                    patched_code=patched,
                    diff_summary="Rewrote dict subscripts to .get() with isinstance guard",
                    llm_reasoning="rule_based",
                )
            # Fallback: rewrite all subscripts
            patched = _apply_transformer(source_code, _RewriteSubscriptToGet())
            if patched and patched != source_code:
                return CodePatch(
                    reason="rule:missing_key:any",
                    original_code=source_code,
                    patched_code=patched,
                    diff_summary="Rewrote all dict subscripts to .get()",
                    llm_reasoning="rule_based",
                )

        if fix_type == "index_out_of_range":
            patched = _apply_transformer(source_code, _RewriteIndexToGuarded())
            if patched and patched != source_code:
                return CodePatch(
                    reason="rule:index_out_of_range",
                    original_code=source_code,
                    patched_code=patched,
                    diff_summary="Guarded literal-int indexing with bounds check",
                    llm_reasoning="rule_based",
                )

        if fix_type == "missing_attribute":
            attr = _extract_attr_from_attributeerror(message)
            if attr:
                patched = _apply_transformer(source_code, _RewriteAttrToGetattr(attr))
                if patched and patched != source_code:
                    return CodePatch(
                        reason=f"rule:missing_attribute:{attr}",
                        original_code=source_code,
                        patched_code=patched,
                        diff_summary=f"Rewrote .{attr} accesses with hasattr guard",
                        llm_reasoning="rule_based",
                    )

        if fix_type == "type_mismatch":
            _ = function_name, signature  # signature/name reserved for future heuristics
            patched = _apply_transformer(source_code, _CoerceNumericBinOps())
            if patched and patched != source_code:
                return CodePatch(
                    reason="rule:type_mismatch:numeric_coercion",
                    original_code=source_code,
                    patched_code=patched,
                    diff_summary="Wrapped numeric BinOp operands with None->0 coercion",
                    llm_reasoning="rule_based",
                )

        if fix_type in {"invalid_value"} and "division" in message.lower():
            patched = _apply_transformer(source_code, _RewriteDivToSafe())
            if patched and patched != source_code:
                return CodePatch(
                    reason="rule:invalid_value:safe_div",
                    original_code=source_code,
                    patched_code=patched,
                    diff_summary="Guarded division operands against 0/None",
                    llm_reasoning="rule_based",
                )

        if diagnosis.error_type == "ZeroDivisionError":
            patched = _apply_transformer(source_code, _RewriteDivToSafe())
            if patched and patched != source_code:
                return CodePatch(
                    reason="rule:zero_division",
                    original_code=source_code,
                    patched_code=patched,
                    diff_summary="Guarded division operands against 0/None",
                    llm_reasoning="rule_based",
                )

        return None


class OpenAICodePatchGenerator(CodePatchGenerator):
    """LLM-backed patch generation with JSON response."""

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o-mini",
        base_url: str | None = None,
    ):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url

    def generate(
        self,
        source_code: str,
        function_name: str,
        signature: CodeFunctionSignature,
        diagnosis: CodeDiagnosis,
        test_cases: list[CodeTestCase],
        history: list[CodePatch] | None = None,
    ) -> CodePatch | None:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ImportError("openai package required for OpenAICodePatchGenerator") from exc

        client_kwargs: dict[str, Any] = {"api_key": self.api_key}
        if self.base_url:
            client_kwargs["base_url"] = self.base_url
        client = OpenAI(**client_kwargs)

        allowed = "json, re, math, datetime, collections, itertools, functools, typing, dataclasses, copy"
        system = (
            "你是 Python 代码修复专家。根据错误与上下文修复给定入口函数。"
            f"规则: 只修改函数体语义, 保持函数签名不变; 仅允许 import: {allowed}; "
            "禁止 eval/exec/open/__import__; 最多 100 行; 输出必须是 JSON: "
            '{"patched_code":"...","reasoning":"...","diff_summary":"..."}'
        )
        user_parts = [
            f"函数名: {function_name}",
            f"签名: {signature.to_dict()}",
            f"诊断: {diagnosis.to_dict()}",
            f"源码:\n{source_code}",
            f"测试用例: {[tc.to_dict() for tc in test_cases]}",
        ]
        if history:
            user_parts.append(f"历史补丁: {[p.to_dict() for p in history]}")
        user = "\n\n".join(user_parts)

        response = client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.3,
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content or "{}"
        data = json.loads(content)
        patched = str(data.get("patched_code") or "").strip()
        if not patched:
            return None
        return CodePatch(
            reason="llm_code_patch",
            original_code=source_code,
            patched_code=patched,
            diff_summary=str(data.get("diff_summary") or ""),
            llm_reasoning=str(data.get("reasoning") or ""),
        )
