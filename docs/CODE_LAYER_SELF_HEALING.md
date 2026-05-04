# ESkill 代码层自修复设计

## 定位

| 层级 | 修复对象 | 当前状态 |
|------|---------|---------|
| 配置层 | 模板、参数、Pipeline 步骤、重试策略 | ✅ 已实现 |
| **代码层** | **Python 函数体、业务逻辑、API 调用** | **本文档** |

配置层自修复改的是 `dict`，代码层自修复改的是**可执行代码**。

---

## 核心架构

```
┌─────────────────────────────────────────────────────────────────┐
│                     CodeSkillRuntime                             │
│                                                                 │
│  1. 加载代码版本 (CodeSkillVersion)                               │
│  2. 沙箱执行 → 质量门控                                           │
│  3. 失败? → 诊断 (CodeDiagnostics)                               │
│  4. LLM 生成修复代码 (CodePatchGenerator)                         │
│  5. AST 安全校验 (CodeValidator)                                  │
│  6. 沙箱验证修复后代码                                             │
│  7. 通过 → 固化为新版本 (CodeSkillVersion v2)                     │
│  8. 不通过 → 回滚                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 数据模型

### CodeSkillVersion

```python
@dataclass
class CodeSkillVersion:
    version: int
    source_code: str                    # Python 源码
    function_name: str                  # 入口函数名
    signature: CodeFunctionSignature    # 函数签名
    dependencies: list[str]             # 允许的 import 列表
    trigger_policy: TriggerPolicy
    quality_gate: dict[str, Any]
    test_cases: list[CodeTestCase]      # 内置测试用例
    created_at: str = ""
    source_run_id: str = ""
```

### CodeFunctionSignature

```python
@dataclass
class CodeFunctionSignature:
    """函数签名 —— 保证修复后接口不变。"""
    params: list[str]           # 参数名列表
    return_type: str            # 返回类型
    required_params: list[str]  # 必需参数
```

### CodeTestCase

```python
@dataclass
class CodeTestCase:
    """代码级测试用例 —— 修复后必须全部通过。"""
    case_id: str
    input_data: dict[str, Any]
    expected_output: dict[str, Any] | None  # None = 只要不抛异常
    assert_fn: str | None = None             # 自定义断言代码
```

### CodePatch

```python
@dataclass
class CodePatch:
    """代码补丁 —— 不是改 dict，是改源码。"""
    reason: str
    original_code: str          # 修复前代码
    patched_code: str           # 修复后代码
    diff_summary: str           # 人类可读的变更说明
    llm_reasoning: str          # LLM 的修复推理过程
```

---

## 核心模块

### 1. CodeValidator —— AST 安全校验

修复后的代码必须通过安全检查才能执行：

```python
class CodeValidator:
    """AST 级别的代码安全校验。"""

    # 禁止的 AST 节点
    FORBIDDEN_NODES = {
        ast.Import,          # 禁止任意 import
        ast.ImportFrom,
        ast.Exec,            # 禁止 exec
        ast.Global,          # 禁止 global
        ast.Attribute,       # 禁止属性访问（如 os.system）
    }

    # 允许的 import 白名单
    ALLOWED_IMPORTS = {
        "json", "re", "math", "datetime",
        "collections", "itertools", "functools",
        "typing", "dataclasses", "copy",
    }

    # 禁止的内置函数
    FORBIDDEN_BUILTINS = {
        "eval", "exec", "compile", "open",
        "__import__", "globals", "locals",
        "getattr", "setattr", "delattr",
        "input", "breakpoint",
    }

    def validate(self, source_code: str) -> CodeValidationResult:
        """校验代码安全性。"""
        # 1. 解析 AST
        # 2. 检查禁止的节点
        # 3. 检查 import 白名单
        # 4. 检查禁止的内置函数
        # 5. 检查函数签名一致性
        # 6. 检查代码复杂度（行数/圈复杂度上限）
        ...
```

### 2. CodeSandbox —— 沙箱执行

在隔离环境中执行修复后的代码：

```python
class CodeSandbox:
    """沙箱执行代码 —— 子进程隔离。"""

    def execute(
        self,
        source_code: str,
        function_name: str,
        input_data: dict[str, Any],
        *,
        timeout_seconds: float = 5.0,
        max_memory_mb: int = 128,
        max_output_size: int = 10000,
    ) -> CodeSandboxResult:
        """在子进程中执行代码。"""
        # 1. 写入临时文件
        # 2. 启动子进程（受限环境）
        # 3. 通过 stdin/stdout 传递输入输出（JSON 序列化）
        # 4. 超时/内存限制
        # 5. 收集结果
        ...
```

**沙箱方案对比：**

| 方案 | 安全性 | 性能 | 实现难度 | 适用场景 |
|------|--------|------|---------|---------|
| subprocess | 中 | 中 | 低 | 通用 |
| Docker 容器 | 高 | 低 | 中 | 生产环境 |
| RestrictedPython | 中 | 高 | 中 | 简单场景 |
| WebAssembly (Pyodide) | 高 | 中 | 高 | 浏览器/边缘 |

**推荐：subprocess 作为默认，Docker 作为生产选项。**

### 3. CodeDiagnostics —— 代码诊断

分析代码失败原因，给 LLM 提供精准上下文：

```python
class CodeDiagnostics:
    """代码失败诊断。"""

    def diagnose(
        self,
        source_code: str,
        function_name: str,
        input_data: dict[str, Any],
        error: Exception,
    ) -> CodeDiagnosis:
        """诊断代码失败原因。"""
        return CodeDiagnosis(
            error_type=type(error).__name__,
            error_message=str(error),
            traceback_str=traceback.format_exc(),
            failing_line=self._extract_failing_line(source_code, error),
            local_variables=self._capture_locals(source_code, input_data, error),
            suggested_fix_type=self._classify_error(error),
        )

    def _classify_error(self, error: Exception) -> str:
        """分类错误类型，指导 LLM 修复方向。"""
        if isinstance(error, KeyError):
            return "missing_key"          # 缺少字典键 → 添加默认值
        elif isinstance(error, TypeError):
            return "type_mismatch"        # 类型不匹配 → 添加类型转换
        elif isinstance(error, ValueError):
            return "invalid_value"        # 值无效 → 添加校验
        elif isinstance(error, AttributeError):
            return "missing_attribute"    # 属性不存在 → 添加检查
        elif isinstance(error, IndexError):
            return "index_out_of_range"   # 索引越界 → 添加边界检查
        elif isinstance(error, TimeoutError):
            return "timeout"              # 超时 → 优化性能/减少计算
        else:
            return "unknown"              # 未知 → 需要更多上下文
```

### 4. CodePatchGenerator —— LLM 代码修复

```python
class CodePatchGenerator:
    """用 LLM 生成代码修复补丁。"""

    SYSTEM_PROMPT = """你是一个 Python 代码修复专家。
你的任务是根据错误信息和上下文，修复给定的 Python 函数。

规则：
1. 只修改函数体，不修改函数签名（参数名、返回类型）
2. 只使用白名单内的 import：{allowed_imports}
3. 不能使用 eval/exec/open/__import__ 等危险函数
4. 代码行数不超过 100 行
5. 必须处理异常，不能抛出未捕获的异常
6. 返回格式必须是 JSON：{{"patched_code": "...", "reasoning": "...", "diff_summary": "..."}}
"""

    def generate(
        self,
        source_code: str,
        function_name: str,
        signature: CodeFunctionSignature,
        diagnosis: CodeDiagnosis,
        test_cases: list[CodeTestCase],
        history: list[CodePatch] | None = None,
    ) -> CodePatch | None:
        """生成代码修复补丁。"""
        prompt = self._build_prompt(
            source_code, function_name, signature,
            diagnosis, test_cases, history,
        )
        response = self.llm_client.chat(prompt)
        return self._parse_response(response)
```

### 5. CodeSkillRuntime —— 代码层运行时

```python
class CodeSkillRuntime:
    """代码层自修复运行时。"""

    def run(
        self,
        skill_id: str,
        input_data: dict[str, Any],
        *,
        force_dynamic: bool = False,
        solidify: bool = True,
    ) -> CodeSkillRun:
        """执行代码技能，失败时自动修复。"""
        skill = self.store.get_code_skill(skill_id)
        version = skill.get_active_version()

        # 1. 沙箱执行当前版本
        result = self.sandbox.execute(
            version.source_code,
            version.function_name,
            input_data,
        )

        if result.success:
            quality = self._check_quality(result.output, version.quality_gate)
            if quality["passed"] and not force_dynamic:
                return CodeSkillRun(stage="static", output=result.output)

        # 2. 诊断失败原因
        diagnosis = self.diagnostics.diagnose(
            version.source_code,
            version.function_name,
            input_data,
            result.error,
        )

        # 3. 领域守卫
        if not self._is_within_domain(skill, input_data):
            return CodeSkillRun(stage="domain_rejected", error="超出领域")

        # 4. LLM 生成修复代码
        patch = self.patch_generator.generate(
            version.source_code,
            version.function_name,
            version.signature,
            diagnosis,
            version.test_cases,
        )

        if not patch:
            return CodeSkillRun(stage="patch_failed", error="无法生成修复")

        # 5. AST 安全校验
        validation = self.code_validator.validate(patch.patched_code)
        if not validation.safe:
            return CodeSkillRun(stage="validation_failed", error=validation.issues)

        # 6. 签名一致性检查
        if not self._signature_matches(patch.patched_code, version.signature):
            return CodeSkillRun(stage="signature_mismatch", error="函数签名不一致")

        # 7. 沙箱验证修复后代码
        test_results = self._run_test_cases(patch.patched_code, version)
        if not all(r.passed for r in test_results):
            return CodeSkillRun(stage="test_failed", error="测试用例未通过")

        # 8. 质量门控
        verify_result = self.sandbox.execute(
            patch.patched_code,
            version.function_name,
            input_data,
        )
        quality = self._check_quality(verify_result.output, version.quality_gate)
        if not quality["passed"]:
            return CodeSkillRun(stage="quality_failed", error="质量门控未通过")

        # 9. 固化新版本
        if solidify:
            new_version = CodeSkillVersion(
                version=version.version + 1,
                source_code=patch.patched_code,
                function_name=version.function_name,
                signature=version.signature,
                dependencies=version.dependencies,
                trigger_policy=version.trigger_policy,
                quality_gate=version.quality_gate,
                test_cases=version.test_cases,
            )
            skill.add_version(new_version)
            self.store.save_code_skill(skill)

        return CodeSkillRun(
            stage="solidified",
            output=verify_result.output,
            patch=patch,
        )
```

---

## 安全体系

### 三层防线

```
第一层：AST 校验
  ├── 禁止危险 import（os, sys, subprocess...）
  ├── 禁止危险内置函数（eval, exec, open...）
  ├── 禁止文件/网络操作
  └── 函数签名一致性检查

第二层：沙箱执行
  ├── 子进程隔离（非主进程）
  ├── 超时限制（默认 5 秒）
  ├── 内存限制（默认 128MB）
  ├── 输出大小限制
  └── 无网络访问（可选）

第三层：回归测试
  ├── 所有历史测试用例必须通过
  ├── 质量门控检查
  └── 人工审核（可选，高风险场景）
```

### 代码复杂度限制

```python
MAX_CODE_LINES = 100          # 单个函数最多 100 行
MAX_CyclOMATIC_COMPLEXITY = 10 # 圈复杂度上限
MAX_NESTING_DEPTH = 4          # 嵌套深度上限
MAX_LOOP_ITERATIONS = 1000     # 循环迭代上限
```

---

## 与配置层自修复的关系

```
┌──────────────────────────────────────────────────────────┐
│  ESkill 统一接口                                          │
│                                                          │
│  ┌─────────────────┐    ┌──────────────────────┐        │
│  │  配置层自修复     │    │  代码层自修复          │        │
│  │  (已实现)        │    │  (本文档)             │        │
│  │                 │    │                      │        │
│  │  修复对象:       │    │  修复对象:            │        │
│  │  - 模板文本      │    │  - Python 函数体      │        │
│  │  - 参数值        │    │  - 业务逻辑代码       │        │
│  │  - Pipeline 步骤 │    │  - API 调用代码       │        │
│  │  - 重试策略      │    │  - 数据处理代码       │        │
│  │                 │    │                      │        │
│  │  安全性: 高      │    │  安全性: 中（需沙箱）  │        │
│  │  灵活性: 中      │    │  灵活性: 高           │        │
│  │  需要 LLM: 可选  │    │  需要 LLM: 必须       │        │
│  └─────────────────┘    └──────────────────────┘        │
│                                                          │
│  自动选择:                                                │
│  - 如果 Skill 是配置型 → 配置层修复                        │
│  - 如果 Skill 是代码型 → 代码层修复                        │
│  - 如果都有 → 先尝试配置层（安全），再尝试代码层（灵活）      │
└──────────────────────────────────────────────────────────┘
```

### 自动选择策略

```python
class HybridSkillRuntime:
    """混合运行时 —— 自动选择配置层或代码层修复。"""

    def run(self, skill_id, input_data, **kwargs):
        skill = self.store.get_skill(skill_id)

        # 判断 Skill 类型
        if skill.skill_type == "config":
            return self.config_runtime.run(skill_id, input_data, **kwargs)
        elif skill.skill_type == "code":
            return self.code_runtime.run(skill_id, input_data, **kwargs)
        else:
            # 混合模式：先配置层，失败再代码层
            result = self.config_runtime.run(skill_id, input_data, **kwargs)
            if result.stage in ("static", "solidified"):
                return result
            # 配置层修复失败，尝试代码层
            return self.code_runtime.run(skill_id, input_data, **kwargs)
```

---

## 使用示例

### 定义代码型 Skill

```python
from eskill import CodeSkillStore, CodeSkillVersion, CodeFunctionSignature, CodeTestCase

store = CodeSkillStore("code_skills.db")

# 定义一个"商品价格计算"的代码型 Skill
source_code = '''
def calculate_price(items, discount=0.0):
    """计算商品总价。"""
    total = sum(item["price"] * item["quantity"] for item in items)
    return {"total": total * (1 - discount), "discount": discount}
'''

signature = CodeFunctionSignature(
    params=["items", "discount"],
    return_type="dict",
    required_params=["items"],
)

test_cases = [
    CodeTestCase(
        case_id="basic",
        input_data={"items": [{"price": 100, "quantity": 2}], "discount": 0.1},
        expected_output={"total": 180.0, "discount": 0.1},
    ),
    CodeTestCase(
        case_id="empty_items",
        input_data={"items": [], "discount": 0},
        expected_output={"total": 0, "discount": 0},
    ),
    CodeTestCase(
        case_id="missing_quantity",
        input_data={"items": [{"price": 50}]},  # 没有 quantity
        expected_output=None,  # 只要不抛异常
    ),
]

version = CodeSkillVersion(
    version=1,
    source_code=source_code,
    function_name="calculate_price",
    signature=signature,
    dependencies=[],
    test_cases=test_cases,
    quality_gate={"required_keys": ["total", "discount"]},
)

store.save_code_skill("price-calculator", "价格计算器", "电商", version)
```

### 执行与自修复

```python
from eskill import CodeSkillRuntime

runtime = CodeSkillRuntime(store, llm_generator=my_llm)

# 正常执行
run = runtime.run("price-calculator", {
    "items": [{"price": 100, "quantity": 2}],
    "discount": 0.1,
})
# stage="static", output={"total": 180.0, "discount": 0.1}

# 遇到新场景：items 里有 None 值
run = runtime.run("price-calculator", {
    "items": [{"price": None, "quantity": 1}],
})
# 静态执行失败 → 诊断(TypeError) → LLM 修复代码 → 沙箱验证 → 固化 v2
# 修复后代码自动处理 None 值
```

### 修复后的代码（LLM 生成）

```python
# v2: LLM 自动修复了 None 值处理
def calculate_price(items, discount=0.0):
    """计算商品总价。"""
    total = 0
    for item in items:
        price = item.get("price") or 0
        quantity = item.get("quantity") or 1
        total += price * quantity
    return {"total": total * (1 - discount), "discount": discount}
```

---

## 实现路线图

### Phase 1：基础代码执行（1-2 天）

- [ ] `CodeSkillVersion` / `CodeTestCase` 数据模型
- [ ] `CodeValidator` AST 安全校验
- [ ] `CodeSandbox` 子进程执行
- [ ] `CodeSkillStore` 存储层

### Phase 2：诊断与修复（2-3 天）

- [ ] `CodeDiagnostics` 错误诊断
- [ ] `CodePatchGenerator` LLM 代码修复
- [ ] `CodeSkillRuntime` 完整运行时
- [ ] 签名一致性检查

### Phase 3：安全加固（1-2 天）

- [ ] Docker 沙箱选项
- [ ] 代码复杂度限制
- [ ] 人工审核流程（高风险场景）
- [ ] 修复审计日志

### Phase 4：混合运行时（1 天）

- [ ] `HybridSkillRuntime` 配置层 + 代码层自动选择
- [ ] 与现有 `ESkillRuntime` 集成
- [ ] 双层架构（Employee + Skill）代码层支持

---

## 风险与对策

| 风险 | 对策 |
|------|------|
| LLM 生成恶意代码 | AST 校验 + 沙箱 + import 白名单 |
| 修复后引入新 Bug | 回归测试 + 质量门控 + 灰度发布 |
| 函数签名被修改 | 签名一致性检查（AST 对比） |
| 无限循环/内存爆炸 | 超时限制 + 内存限制 + 子进程隔离 |
| LLM 幻觉（编造不存在的 API） | import 白名单 + 沙箱执行验证 |
| 修复链过长（v1→v2→v3...） | 最大版本数限制 + 人工审核触发 |

---

## 实现状态（eskill-prototype）

以下已在 `src/eskill/code/` 与主包中落地（对应路线图 Phase 1+2+4 核心闭环；Docker 沙箱与人工审核未实现）：

| 组件 | 状态 |
|------|------|
| `CodeSkillVersion` / `CodeTestCase` / `CodePatch` 等模型 | 已实现（`eskill.code.models`） |
| `CodeValidator`（AST、import 白名单、危险内置、签名字段） | 已实现 |
| `CodeSandbox`（`multiprocessing` spawn、超时、输出大小、POSIX 内存软限制） | 已实现 |
| `JsonCodeSkillStore` | 已实现 |
| `CodeDiagnostics` | 已实现 |
| `RuleBasedCodePatchGenerator` / `OpenAICodePatchGenerator` | 已实现 |
| `CodeSkillRuntime` | 已实现（可写入主 `JsonSkillStore` 的 `EvolutionEvent`） |
| `HybridSkillRuntime`（`ESkill.skill_type`: config / code / hybrid） | 已实现 |
| 架构层自动调整（`ArchitectureProfile` + `ArchitectureAdjuster` + `ArchitectureExecutor`） | 已实现；`ESkillRuntime._execute_static` 按 `architecture_profile` 包装执行并记录 `architecture_*` 事件与 metrics |

示例：`python examples/code_layer_demo.py`。
