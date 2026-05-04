# MODstore × ESkill 架构升级方案

## 目标

**让工作流 NL 生成时，直接产出 Python 代码（code-layer），每个节点自动被 ESkill 包装，获得代码层自修复能力。**

---

## 现状

```
用户输入: "帮我做一个天气查询员工"
      ↓
LLM 生成 JSON
  ├── skill_blueprints: 只能生成 static_logic（模板/Pipeline）
  │     {temp_skill_id: "skill_query", static_logic: {type: "template_transform"}}
  └── workflow.nodes: 每个节点引用一个 blueprint
        {node_type: "eskill", config: {skill_id: 123}}
      ↓
_create_generated_skills() 落库为 ESkill 表（只有配置层）
```

**问题：**

1. LLM 生成的 Skill 只是模板文本，不是真正的 Python 代码
2. 没有 code-layer 自修复能力
3. LLM 不知道什么时候该生成模板，什么时候该生成代码

---

## 目标架构

```
用户输入: "帮我做一个天气查询员工"
      ↓
LLM 生成 JSON（支持 Python 代码层）
  ├── code_skills:  [
  │     {
  │       temp_skill_id: "skill_query_weather",
  │       source_code: "def query_weather(city: str) -> dict: ...",
  │       function_name: "query_weather",
  │       dependencies: ["requests"],
  │       test_cases: [{input: {"city": "北京"}, expected_output: {...}}],
  │     },
  │   ]
  └── workflow.nodes:
        {node_type: "eskill", config: {temp_skill_id: "skill_query_weather", layer: "code"}}
      ↓
_create_generated_skills() 落库为 CodeSkill（code_skills 表）
      ↓
eskill_runtime 检测到 layer="code" → CodeSkillRuntime 执行
      ↓
执行失败 → AST 校验 → 沙箱验证 → 回归测试 → 固化新版本 v2
```

---

## 数据库变更

### 新增表：code_skills

```sql
CREATE TABLE code_skills (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id),
    name        TEXT NOT NULL,
    domain      TEXT,
    description TEXT,
    active_version INTEGER DEFAULT 1,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
);
```

### 新增表：code_skill_versions

```sql
CREATE TABLE code_skill_versions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    code_skill_id   INTEGER NOT NULL REFERENCES code_skills(id),
    version         INTEGER NOT NULL,
    source_code     TEXT NOT NULL,
    function_name   TEXT NOT NULL,
    signature       TEXT,             -- JSON: params, return_type, required_params
    dependencies    TEXT,             -- JSON: ["requests", "json"]
    quality_gate    TEXT,             -- JSON
    test_cases      TEXT,             -- JSON: [{input, expected_output, assert_fn}]
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    source_run_id   TEXT,
);
```

### 新增表：code_skill_runs

```sql
CREATE TABLE code_skill_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    code_skill_id   INTEGER NOT NULL REFERENCES code_skills(id),
    run_id          TEXT NOT NULL,
    stage           TEXT NOT NULL,
    input_data      TEXT,             -- JSON
    output_data     TEXT,             -- JSON
    patch           TEXT,             -- JSON: original_code, patched_code, diff_summary
    error           TEXT,
    duration_ms     REAL,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
);
```

### 现有表复用


| 现有表               | 复用方式          |
| ----------------- | ------------- |
| `eskills`         | 配置层 Skill（保留） |
| `eskill_versions` | 配置层版本（保留）     |
| `eskill_runs`     | 配置层运行记录（保留）   |


**配置层 vs 代码层共存，自动选择。**

---

## 工作流 NL 生成变更

### 1. System Prompt 升级

```
你现在是一个「全栈 Skill 生成器」，需要同时生成 skill_blueprints 和 workflow。

关键变更：
1. 对于简单模板转换，使用 skill_blueprints（配置层）
2. 对于需要计算、API 调用、数据处理的逻辑，使用 code_skills（代码层）
3. 每个 code_skill 必须包含：
   - source_code: Python 源码（def xxx(...) -> dict: 格式）
   - function_name: 入口函数名
   - dependencies: 允许的 import 列表
   - test_cases: 至少 2 个测试用例
4. source_code 规则：
   - 只能使用标准库和 dependencies 中的模块
   - 不能使用 eval/exec/open/__import__/input
   - 不能操作文件/网络（API 调用通过内置工具）
   - 函数签名必须包含类型提示
   - 返回 dict 格式，不能返回原始字符串
   - 必须处理异常，不能抛出未捕获的异常
5. workflow 节点 config 中 layer 字段：
   - "config": 配置层 Skill
   - "code": 代码层 Skill
```

### 2. 输出格式升级

```json
{
  "skill_blueprints": [
    {
      "temp_skill_id": "skill_format_greeting",
      "name": "问候语格式化",
      "domain": "用户交互",
      "static_logic": {
        "type": "template_transform",
        "template": "你好，${name}！${message}"
      },
      "quality_gate": {"required_keys": ["greeting"]},
      "domain_keywords": ["你好", "欢迎"]
    }
  ],
  "code_skills": [
    {
      "temp_skill_id": "skill_query_user_profile",
      "name": "查询用户画像",
      "domain": "用户数据",
      "source_code": "def query_user_profile(user_id: str) -> dict:\n    ...\n    return {\"profile\": ..., \"error\": None}",
      "function_name": "query_user_profile",
      "dependencies": ["json"],
      "test_cases": [
        {"input": {"user_id": "u123"}, "expected_output": {"profile": {...]}},
        {"input": {"user_id": ""}, "expected_output": null}
      ],
      "quality_gate": {"required_keys": ["profile"]},
      "domain_keywords": ["用户", "画像", "资料"]
    }
  ],
  "workflow": {
    "nodes": [
      {"node_type": "start"},
      {"node_type": "eskill", "config": {"temp_skill_id": "skill_format_greeting", "layer": "config"}},
      {"node_type": "eskill", "config": {"temp_skill_id": "skill_query_user_profile", "layer": "code"}},
      {"node_type": "end"}
    ],
    "edges": [...]
  }
}
```

---

## 运行时集成

### 1. eskill_runtime.py 变更

```python
class ESkillRuntime:
    def run(self, skill_id: int, input_data: dict, layer: str = "auto", ...):
        # 1. 尝试代码层
        if layer in ("code", "auto"):
            result = self._try_code_layer(skill_id, input_data, ...)
            if result is not None:
                return result

        # 2. 降级到配置层
        return self._run_config_layer(skill_id, input_data, ...)

    def _try_code_layer(self, skill_id, input_data, ...):
        """尝试代码层执行。"""
        # 1. 查询 code_skills + code_skill_versions
        code_skill = self.db.query(CodeSkill).get(skill_id)
        if not code_skill:
            return None

        version = code_skill.get_active_version()

        # 2. 沙箱执行
        result = self.sandbox.execute(version.source_code, version.function_name, input_data)
        if result.success and self._check_quality(result.output, version.quality_gate):
            return CodeSkillRun(stage="static", output=result.output)

        # 3. 失败 → 自动修复
        return self.code_runtime.run(skill_id, input_data, ...)

    def _run_config_layer(self, skill_id, input_data, ...):
        """配置层执行（原有逻辑）。"""
        # ... 保持不变 ...
```

### 2. workflow_engine.py 变更

```python
def _execute_eskill_node(self, node, data, config, *, session, workflow_id, user_id=0):
    # 新增 layer 字段
    layer = config.get("layer", "auto")  # "config" | "code" | "auto"

    # 传入 layer 给 runtime
    result = default_eskill_runtime.run(
        session,
        eskill_id=eskill_id,
        user_id=user_id,
        input_data=input_data,
        workflow_id=workflow_id,
        workflow_node_id=node.id,
        logic_overrides=logic_overrides,
        trigger_policy_override=config.get("trigger_policy") or {},
        quality_gate_override=config.get("quality_gate") or {},
        force_dynamic=bool(config.get("force_dynamic")),
        solidify=bool(config.get("solidify", True)),
        layer=layer,  # ← 新增
    )
```

### 3. workbench_api.py 变更

```python
@app.post("/api/eskills", response_model=ESkillPublic)
def _api_create_eskill(data: ESkillCreate, db: Session, *, user: User = Depends(require_user)):
    if data.skill_type == "code":
        # 创建代码层 Skill
        skill = CodeSkill(
            user_id=user.id,
            name=data.name,
            domain=data.domain or "",
            description=data.description or "",
        )
        version = CodeSkillVersion(
            code_skill_id=skill.id,
            version=1,
            source_code=data.source_code,
            function_name=data.function_name,
            signature=...,
            dependencies=data.dependencies or [],
            test_cases=data.test_cases or [],
            quality_gate=data.quality_gate or {},
        )
    else:
        # 配置层 Skill（原有逻辑）
        skill = ESkill(...)
        version = ESkillVersion(...)

    db.add(skill)
    db.commit()
    db.refresh(skill)
    return skill
```

---

## 创建流程变更

### workflow_nl_graph.py 变更

```python
def _normalize_code_skills(data: Dict[str, Any], warnings: List[str]) -> List[Dict[str, Any]]:
    raw = data.get("code_skills")
    if not isinstance(raw, list):
        return []
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for idx, item in enumerate(raw[:_MAX_CODE_SKILLS]):
        temp_id = _as_identifier(item.get("temp_skill_id") or item.get("id"), f"code_skill_{idx + 1}")
        if temp_id in seen:
            warnings.append(f"重复 temp_skill_id {temp_id!r}，已跳过")
            continue
        seen.add(temp_id)

        # 校验 source_code
        source_code = str(item.get("source_code") or "").strip()
        if not source_code:
            warnings.append(f"code_skill {temp_id!r} 缺少 source_code")
            continue

        # AST 预校验
        try:
            tree = ast.parse(source_code)
        except SyntaxError as e:
            warnings.append(f"code_skill {temp_id!r} 语法错误: {e}")
            continue

        out.append({
            "temp_skill_id": temp_id,
            "name": str(item.get("name") or temp_id).strip()[:128],
            "domain": str(item.get("domain") or "").strip()[:2000],
            "description": str(item.get("description") or "").strip()[:4000],
            "source_code": source_code,
            "function_name": str(item.get("function_name") or "main").strip(),
            "dependencies": _safe_list(item.get("dependencies")),
            "test_cases": _safe_list(item.get("test_cases")),
            "quality_gate": _safe_dict(item.get("quality_gate")),
            "domain_keywords": _safe_list(item.get("domain_keywords")),
        })
    return out


def _create_generated_code_skills(
    db: Session,
    user: User,
    code_skills: List[Dict[str, Any]],
    warnings: List[str],
) -> Dict[str, int]:
    temp_to_skill: Dict[str, int] = {}
    for bp in code_skills:
        temp_id = str(bp.get("temp_skill_id") or "").strip()
        name = str(bp.get("name") or temp_id or "Generated").strip()[:128]
        if not temp_id:
            continue

        existing = db.query(CodeSkill).filter(
            CodeSkill.user_id == user.id,
            CodeSkill.name == name,
        ).first()
        if existing:
            temp_to_skill[temp_id] = int(existing.id)
            warnings.append(f"Code Skill {name!r} 已存在，复用 code_skill_id={existing.id}")
            continue

        skill = CodeSkill(
            user_id=user.id,
            name=name,
            domain=str(bp.get("domain") or "").strip()[:2000],
            description=str(bp.get("description") or "").strip()[:4000],
        )
        db.add(skill)
        db.flush()

        version = CodeSkillVersion(
            code_skill_id=skill.id,
            version=1,
            source_code=bp["source_code"],
            function_name=bp["function_name"],
            signature={"params": [], "return_type": "dict", "required_params": []},
            dependencies=bp.get("dependencies") or [],
            quality_gate=bp.get("quality_gate") or {},
            test_cases=bp.get("test_cases") or [],
        )
        db.add(version)

        temp_to_skill[temp_id] = int(skill.id)

    return temp_to_skill
```

### NL 生成流程

```python
def generate_workflow_from_nl(db: Session, user: User, text: str):
    # 1. LLM 生成（同时输出 skill_blueprints + code_skills）
    data = _llm_generate(db, user, text)

    # 2. 解析
    skill_blueprints = _normalize_skill_blueprints(data, warnings)
    code_skills = _normalize_code_skills(data, warnings)
    workflow = _normalize_workflow(data, warnings)

    # 3. 创建 Skills
    config_temp_to_skill = _create_generated_skills(db, user, skill_blueprints, warnings)
    code_temp_to_skill = _create_generated_code_skills(db, user, code_skills, warnings)

    # 4. 落库工作流
    # 5. 校验节点引用的 skill_id / code_skill_id
    # ...
```

---

## Node Registry 变更

### 前端 useNodeRegistry.ts

```typescript
// 新增 layer 选项
eskill: {
  label: 'ESkill (自修复技能)',
  icon: '⚡',
  config: {
    layer: {
      type: 'select',
      options: [
        { value: 'auto', label: '自动选择 (推荐)' },
        { value: 'config', label: '配置层 (模板/Pipeline)' },
        { value: 'code', label: '代码层 (Python 代码)' },
      ],
      default: 'auto',
    },
    skill_id: { type: 'skill-picker', required: true },
    code_skill_id: { type: 'code-skill-picker', show_when: 'layer === code' },
    output_var: { type: 'string', default: 'eskill_output' },
  },
}
```

---

## 安全策略

### 代码层生成安全限制

1. **import 白名单**：LLM 生成的 `dependencies` 只能是：
  ```
   json, re, math, datetime, collections, itertools, functools,
   typing, dataclasses, copy, decimal, hashlib, uuid, logging,
   requests, httpx (如果允许外部调用)
  ```
2. **AST 预校验**：生成后立刻用 `CodeValidator` 校验：
  ```python
   validation = CodeValidator.validate(source_code)
   if not validation.safe:
       raise ValueError(f"生成的代码不安全: {validation.issues}")
  ```
3. **沙箱测试**：生成后立刻在沙箱中执行测试用例：
  ```python
   for tc in test_cases:
       result = sandbox.execute(source_code, function_name, tc.input)
       if not result.passed:
           raise ValueError(f"测试用例未通过: {tc.case_id}")
  ```

---

## 实施路线图

### Phase 1：数据库层（0.5 天）

- 新增 `code_skills` 表
- 新增 `code_skill_versions` 表
- 新增 `code_skill_runs` 表
- SQLAlchemy Model 定义

### Phase 2：NL 生成层（1 天）

- `_normalize_code_skills()` — 解析 code_skills JSON
- `_create_generated_code_skills()` — 落库
- System Prompt 升级 — 支持 Python 代码生成
- AST 预校验集成

### Phase 3：运行时层（1 天）

- `eskill_runtime.py` 支持 layer 参数
- `_try_code_layer()` — 代码层执行
- `workflow_engine.py` 传递 layer 参数
- 自动选择逻辑（config → code → hybrid）

### Phase 4：API 与前端（1 天）

- `workbench_api.py` 支持 code_skill 创建
- 前端 node registry 新增 layer 选项
- code-skill-picker 组件
- 属性面板更新

### Phase 5：测试与验证（0.5 天）

- NL 生成 code_skills 的端到端测试
- 代码层执行测试
- 自动修复测试
- 安全校验测试

---

## 总结


| 维度     | 现状          | 升级后                         |
| ------ | ----------- | --------------------------- |
| 生成内容   | 模板/Pipeline | 模板/Pipeline + **Python 代码** |
| 执行层    | 配置层         | 配置层 + **代码层（自动选择）**         |
| 自修复    | 配置层自修复      | 配置层 + **代码层自修复**            |
| 安全性    | 模板安全        | AST 校验 + **沙箱执行**           |
| LLM 生成 | 文本模板        | **Python 源码**               |


