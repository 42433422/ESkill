"""AST-level validation for patched Python source."""

from __future__ import annotations

import ast

from .models import CodeFunctionSignature, CodeValidationResult

FORBIDDEN_BUILTINS = frozenset({
    "eval",
    "exec",
    "compile",
    "open",
    "__import__",
    "globals",
    "locals",
    "getattr",
    "setattr",
    "delattr",
    "input",
    "breakpoint",
})

ALLOWED_IMPORT_MODULES = frozenset({
    "json",
    "re",
    "math",
    "datetime",
    "collections",
    "itertools",
    "functools",
    "typing",
    "dataclasses",
    "copy",
})

# Safe methods on dict/list/str/tuple/set or generic objects in typical skill code
_SAFE_ATTR_METHODS = frozenset({
    "get",
    "setdefault",
    "keys",
    "values",
    "items",
    "append",
    "extend",
    "insert",
    "pop",
    "clear",
    "strip",
    "split",
    "join",
    "lower",
    "upper",
    "replace",
    "startswith",
    "endswith",
    "format",
    "count",
    "index",
    "add",
    "copy",
    "update",
})

MAX_CODE_LINES = 100


class CodeValidator:
    """AST safety checks for user-supplied Python."""

    def __init__(
        self,
        *,
        allowed_imports: frozenset[str] | None = None,
        max_lines: int = MAX_CODE_LINES,
    ):
        self._allowed_imports = allowed_imports or ALLOWED_IMPORT_MODULES
        self._max_lines = max_lines

    def validate(
        self,
        source_code: str,
        *,
        function_name: str,
        signature: CodeFunctionSignature,
        dependencies: list[str] | None = None,
    ) -> CodeValidationResult:
        issues: list[str] = []
        deps = set(dependencies or [])
        allowed_mods = frozenset(self._allowed_imports & deps) if deps else self._allowed_imports

        lines = source_code.count("\n") + 1 if source_code.strip() else 0
        if lines > self._max_lines:
            issues.append(f"code_too_long:{lines}>{self._max_lines}")

        try:
            tree = ast.parse(source_code)
        except SyntaxError as exc:
            return CodeValidationResult(safe=False, issues=[f"syntax_error:{exc}"])

        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                ok = self._check_import(node, allowed_mods)
                if not ok:
                    issues.append(f"disallowed_import:{ast.dump(node, include_attributes=False)}")
            elif isinstance(node, ast.Global):
                issues.append("forbidden_global")

        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    if node.func.id in FORBIDDEN_BUILTINS:
                        issues.append(f"forbidden_builtin:{node.func.id}")
                elif isinstance(node.func, ast.Attribute) and not self._allowed_attribute_call(
                    node.func, allowed_mods
                ):
                    issues.append(
                        f"disallowed_attribute_call:{ast.dump(node.func, include_attributes=False)}"
                    )

        sig_issues = self._check_signature(tree, function_name, signature)
        issues.extend(sig_issues)

        return CodeValidationResult(safe=not issues, issues=issues)

    def _allowed_attribute_call(self, node: ast.Attribute, allowed_mods: frozenset[str]) -> bool:
        if node.attr in _SAFE_ATTR_METHODS:
            return True
        root = self._attribute_root_name(node)
        return bool(root and root in allowed_mods)

    def _attribute_root_name(self, node: ast.Attribute | ast.Name) -> str | None:
        cur: ast.expr = node
        while isinstance(cur, ast.Attribute):
            cur = cur.value
        if isinstance(cur, ast.Name):
            return cur.id
        return None

    def _check_import(self, node: ast.Import | ast.ImportFrom, allowed_mods: frozenset[str]) -> bool:
        if isinstance(node, ast.Import):
            for alias in node.names:
                base = (alias.name or "").split(".")[0]
                if base not in allowed_mods:
                    return False
            return True
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            base = mod.split(".")[0] if mod else ""
            if node.level and node.level > 0:
                return False
            return not (base and base not in allowed_mods)
        return False

    def _check_signature(
        self,
        tree: ast.AST,
        function_name: str,
        signature: CodeFunctionSignature,
    ) -> list[str]:
        issues: list[str] = []
        func_def: ast.FunctionDef | None = None
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == function_name:
                func_def = node
                break
        if func_def is None:
            issues.append(f"missing_function:{function_name}")
            return issues

        args = func_def.args
        posonly = [a.arg for a in args.posonlyargs]
        pos = [a.arg for a in args.args]
        kwonly = [a.arg for a in args.kwonlyargs]
        all_pos_names = posonly + pos
        declared = set(all_pos_names) | set(kwonly)
        for req in signature.required_params:
            if req not in declared:
                issues.append(f"missing_param:{req}")
        for name in signature.params:
            if name not in declared:
                issues.append(f"signature_mismatch_param:{name}")
        return issues
