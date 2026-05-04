# ESkill

**会自己修 Bug 的 AI 技能**

> 一个 Skill 失败了怎么办？别的框架让你写 try-catch，ESkill **自己修复自己**，然后把修复结果固化为新版本。

```
静态执行失败 → 质量门控检测 → 自动修复 → 固化新版本 → 继续进化
```

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![Stars](https://img.shields.io/github/stars/your-org/eskill.svg)](https://github.com/your-org/eskill/stargazers)

---

## 为什么需要 ESkill？

### 传统 AI Skill 的问题

| 框架 | Skill 坏了怎么办？ | 能自动升级吗？ | 有质量门控吗？ |
|------|-------------------|--------------|---------------|
| LangChain / MCP | 抛异常，用户自己处理 | ❌ | ❌ |
| AutoGPT / CrewAI | 重试几次，然后放弃 | ❌ | ❌ |
| OpenAI Assistants | 依赖模型重新生成 | 半自动 | ❌ |
| **ESkill** | **自动修复 + 固化新版本** | **✅ 自动** | **✅ 5 维度检查** |

### ESkill 做了什么

```
传统方式:                         ESkill:

用户调用 Skill                    用户调用 ESkill
    │                                │
    ▼                                ▼
执行固定代码                     执行静态版本（快、稳定）
    │                                │
    ▼                                ▼
❌ 报错了!                       ❌ 出错了?
    │                                │
    ▼                                ▼
用户改代码，发新版本              自动修复 (规则 / LLM)
    │                                │
    ▼                                ▼
测试、审核、上线                  质量门控检查
    │                                │
    ▼                                ▼
(可能已经过了好几天)              ✅ 修复成功，固化为 v2
                                   │
                                   ▼
                                下次执行用新版本
```

## ✨ 核心特性

### 1. 自修复能力

```python
from eskill import ESkillRuntime, JsonSkillStore

store = JsonSkillStore("skills.json")
# ... 注册一个 Skill ...

# 第一次：静态执行（快）
run = runtime.run("my-skill", {"q": "hello"})
print(run.stage)  # "static"

# 遇到新场景，静态逻辑不够用了？
# ESkill 会自动进入动态修复模式！
run = runtime.run("my-skill", {"q": "复杂的新问题"})
print(run.stage)  # "dynamic" → "solidified"
# 修复成功，自动保存为新版本
```

### 2. 双层进化架构 🆕

```
┌─────────────────────────────────────┐
│  Employee 层（员工壳）               │
│  perception/memory/cognition/action │
│  每一层都能自修复、进化               │
│              ↕ 升级传播              │
└─────────────────────────────────────┘
              │
┌─────────────────────────────────────┐
│  Skill 层（工作流节点）              │
│  每个节点独立自修复                  │
│  修复后固化为新版本                  │
└─────────────────────────────────────┘
```

**叫法**：整条工作流 = 多颗 **Skill** 的编排，产品侧也可称 **Skill 组**（与「工作流节点 = Skill」同一口径）。员工会升级，技能也会升级，两层互相影响、协同进化。

### 3. 有 LLM 更强，没 LLM 也能用

| 场景 | 行为 |
|------|------|
| 有 OpenAI Key | 规则修复 → LLM 智能修复 → 固化 |
| 没有 LLM | 规则修复 → 降级策略 → 记录失败 |
| 生产环境 | 自动选择最佳方案 |

### 4. 领域守卫

动态修复不是万能的。ESkill 会检查：**这个失败是不是还属于 Skill 的业务范围？**

```python
# Skill 定义：处理"天气查询"
domain_keywords = ["天气", "温度", "降水"]

# 用户输入："帮我写首诗"
# ❌ ESkill 拒绝修复：超出领域范围
```

### 5. 5 维度质量门控

修复后的代码能直接用？先过质量检查：

- **min_length**：输出不能太短
- **required_keys**：必须包含关键字段
- **contains_all**：必须包含所有内容
- **contains_any**：至少包含部分内容
- **min_score**：最低分数要求

## 🚀 快速开始

```bash
# 安装
pip install -e ".[test]"

# 跑个 Demo
python examples/demo.py

# 双层架构示例
python examples/dual_layer_example.py

# 跑测试
pytest
```

### 3 分钟创建你的第一个自修复 Skill

```python
from eskill import JsonSkillStore, SkillBlueprint, SkillCreator, ESkillRuntime

# 1. 创建存储
store = JsonSkillStore("skills.json")

# 2. 定义 Skill
bp = SkillBlueprint.template_transform(
    skill_id="weather-query",
    name="天气查询",
    domain="天气查询助手",
    template="当前${city}的天气是${condition}，温度${temp}°C",
    required_fields=["city"],
    output_var="result",
    domain_keywords=["天气", "温度"],  # 领域守卫关键词
    quality_gate={"min_length": 10},   # 质量门控
)

# 3. 注册
SkillCreator.create(store, bp)

# 4. 执行（自动修复！）
runtime = ESkillRuntime(store)
run = runtime.run("weather-query", {"city": "北京"})
print(f"阶段: {run.stage}")      # static
print(f"输出: {run.output_data}")
```

## 📦 生产级功能

| 功能 | 状态 |
|------|------|
| 错误码体系 | ✅ |
| 异常处理（重试/超时/降级） | ✅ |
| 异步支持 (AsyncESkillRuntime) | ✅ |
| SQLite 后端存储 | ✅ |
| 结构化日志 | ✅ |
| 线程安全 (JSON Store + Lock) | ✅ |
| 审计日志 (EvolutionEvent) | ✅ |
| 技能测试框架 (TestCase/Suite/Runner) | ✅ |
| 健康检查与自动降级 | ✅ |
| 自适应策略引擎 (Q-Learning) | ✅ |
| 技能结晶与分层记忆 | ✅ |
| 离线技能包与市场共享 | ✅ |
| 跨 Skill Pipeline 编排 | ✅ |
| 双层进化架构 (Employee + Skill) | ✅ |

## 📁 项目结构

```
eskill-prototype/
├── src/eskill/
│   ├── models.py          # 数据模型
│   ├── runtime.py         # 运行时核心（静态→动态→固化）
│   ├── async_runtime.py   # 异步支持
│   ├── store.py           # JSON 存储
│   ├── sqlite_store.py    # SQLite 存储
│   ├── wrapper.py         # Skill 包装器
│   ├── adapter.py         # 适配协议层
│   ├── llm_adapter.py     # LLM 补丁生成器
│   ├── resilience.py      # 异常处理（重试/超时/降级）
│   ├── errors.py          # 统一错误码
│   ├── employee_layer.py  # 🔥 Employee 层自修复
│   ├── skill_node_layer.py # 🔥 Skill 层自修复
│   ├── dual_layer_bridge.py # 🔥 双层桥接
│   ├── testing.py         # 测试框架
│   ├── health.py          # 健康检查
│   ├── policy.py          # 自适应策略
│   ├── crystal.py         # 技能结晶
│   ├── memory.py          # 分层记忆
│   ├── market.py          # 技能市场
│   ├── pipeline.py        # Pipeline 编排
│   └── ...
├── examples/
│   ├── demo.py
│   ├── dual_layer_example.py  # 双层架构示例
│   └── ...
└── tests/
```

## 🔌 与 MODstore 集成

ESkill 已集成到 [MODstore](https://github.com/your-org/modstore) 工作流引擎中：

```python
# MODstore 工作流引擎中已有 eskill 节点类型
# 工作流节点类型: start, end, employee, condition,
#                openapi_operation, knowledge_search,
#                webhook_trigger, cron_trigger,
#                variable_set, eskill ← 自修复节点
```

每个节点都能自修复，整个工作流就是会进化的 AI 员工。

## 📊 性能基准

```bash
python -m pytest tests/test_benchmark.py -v
```

| 操作 | 耗时 (μs) | 说明 |
|------|-----------|------|
| save_skill | ~50 | JSON 写入 |
| get_skill | ~30 | JSON 读取 |
| static run | ~100 | 静态执行 |
| dynamic run | ~5000 | 动态修复（无 LLM） |
| wrapper execute | ~200 | 包装层开销 |

## 🔒 安全边界

ESkill 的动态阶段**不执行任意代码**：

- 动态修复只生成结构化配置补丁
- Pipeline 只能调用白名单内置工具
- 领域守卫防止越界修复
- 质量门控验证后才能固化

## 🤝 贡献

```bash
# 开发环境
pip install -e ".[test,dev]"

# 代码检查
ruff check src/
ruff format src/
mypy src/eskill/

# 测试
pytest --cov=eskill

# 提交 PR 前
pytest && ruff check src/ && mypy src/eskill/
```

## 📝 License

MIT

---

> **觉得有用？点个 Star ⭐ 让更多人看到！**
