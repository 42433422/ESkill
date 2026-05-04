# Vibe Coding for ESkill

`eskill.vibe_coding` 是 eskill-prototype 的"自然语言到自修复 Skill"层。它把
现有的 `CodeSkillRuntime` / `CodeSandbox` / `CodeValidator` / `ESkillRuntime` /
`ESkillNodeWrapper` 全部串起来，让用户只用一句话就能拿到一个**已沙箱验证、可
自动修复并固化的可执行 Skill**，并把多个 Skill 编排成完整工作流。

> 同一份能力还在 `E:\成都修茈科技有限公司\vibe-coding\` 提供独立 Python 包形态，
> 用于研究、升级与跨项目复用。两边以同步脚本保持一致。

---

## 架构关系

```
┌──────────────────────── eskill.vibe_coding ────────────────────────┐
│                                                                    │
│   VibeCoder（facade.py）一站式入口                                  │
│      │                                                             │
│      ├─ NLCodeSkillFactory ──► CodeValidator + CodeSandbox（生成时验证）│
│      │                       └► JsonCodeSkillStore（v1 落库）        │
│      │                                                             │
│      ├─ NLConfigSkillFactory ─► SkillBlueprint + SkillCreator        │
│      │                       └► JsonSkillStore                      │
│      │                                                             │
│      ├─ NLWorkflowFactory ───► 调 NLCodeSkillFactory 生成每个节点    │
│      │                       └► VibeWorkflowGraph                   │
│      │                                                             │
│      ├─ VibeWorkflowEngine ──► CodeSkillRuntime（节点级自修复）      │
│      │                       └► ESkillRuntime（配置层节点）          │
│      │                                                             │
│      └─ PatchLedger ────────► history / rollback / evolution_chain  │
│                                                                    │
└────────────────────────────────────────────────────────────────────┘
```

**核心约束**：vibe_coding 只负责"NL → 已验证 Skill"和"工作流编排"，**所有
自修复 / 沙箱 / 固化 / 双层架构**全部继承自 eskill 已有模块——不重写、不分叉。

---

## 一页纸 API 速查

```python
from eskill.vibe_coding import VibeCoder, MockLLM, OpenAILLM

# 离线测试
coder = VibeCoder(llm=MockLLM([...预设 JSON 响应...]), store_dir="./data")

# 生产使用
coder = VibeCoder(
    llm=OpenAILLM(api_key="sk-...", model="gpt-4o-mini"),
    store_dir="./data",
)

# === 生成 ===
skill = coder.code("把字符串反转")              # CodeSkill（已沙箱验证）
skill = coder.config_skill("问候技能")          # ESkill（配置层）
graph = coder.workflow("天气查询员工 + 穿衣建议")  # VibeWorkflowGraph

# === 执行（带自修复）===
run = coder.run(skill.skill_id, {"text": "hello"})    # CodeSkillRun
result = coder.execute(graph, {"city": "Beijing"})    # WorkflowRunResult

# === 审计 ===
chain = coder.evolution_chain(skill.skill_id)         # v1 → v2 → ...
history = coder.history(skill.skill_id)               # list[PatchRecord]
coder.rollback(skill.skill_id, target_version=1)
report = coder.report()                               # 跨 Skill 健康度
```

---

## 与 ESkill / CodeSkill 的关系

| 角色 | 职责 | 在 vibe_coding 中谁用它 |
| --- | --- | --- |
| `CodeSkill` / `CodeSkillVersion` | 不可变的代码层版本快照 | `NLCodeSkillFactory` 生成；`PatchLedger.evolution_chain` 读取 |
| `CodeValidator` | AST 安全白名单（import / builtins / 签名一致性） | `NLCodeSkillFactory.generate` 在每轮重试前调用 |
| `CodeSandbox` | spawn 子进程执行 + 超时 + 内存限制 | 同上 |
| `CodeSkillRuntime` | 运行时自修复 + 固化 v2 | `VibeCoder.run` / `VibeWorkflowEngine` 直接调用 |
| `ESkill` / `SkillBlueprint` | 配置层 Skill | `NLConfigSkillFactory` 生成 |
| `ESkillRuntime` | 配置层运行时 | `VibeCoder.config_skill + run` |
| `ESkillNodeWrapper` | 工作流节点级自修复包装 | `VibeWorkflowEngine` 可选启用（双层桥接） |
| `JsonCodeSkillStore` / `JsonSkillStore` | 持久化 | `VibeCoder` 自动管理 |

---

## 四套 Prompt 调优入口

`eskill.vibe_coding.nl.prompts` 是一个独立文件，便于团队后续 A/B 调优：

- `CODE_DIRECT_PROMPT` — 一轮直接出代码
- `BRIEF_FIRST_SPEC_PROMPT` — 双轮：第一轮只写 spec + test_cases
- `BRIEF_FIRST_CODE_PROMPT` — 双轮：第二轮按 spec 写代码
- `CODE_REPAIR_PROMPT` — 失败时让 LLM 修复函数体
- `WORKFLOW_PROMPT` — 一句话出工作流 JSON

---

## CLI

```bash
# 生成代码 Skill（默认 brief-first）
python -m eskill.vibe_coding --mock code "make a demo skill"

# 生成完整工作流
python -m eskill.vibe_coding code "..." --mode direct
python -m eskill.vibe_coding workflow "..."

# 运行 / 回滚 / 审计
python -m eskill.vibe_coding run <skill_id> '{"text":"hello"}'
python -m eskill.vibe_coding rollback <skill_id> 1
python -m eskill.vibe_coding history <skill_id>
python -m eskill.vibe_coding report
python -m eskill.vibe_coding list
```

`--mock` 用内置离线响应跑通主链路，方便冒烟测试；不带 `--mock` 时走
`OPENAI_API_KEY` + `OpenAILLM`。

---

## 离线 Demo

`examples/` 下 5 个文件全部用 `MockLLM`，无需 API Key 即可跑通：

```bash
python examples/vibe_coding_minimal.py        # NL → CodeSkill 最小路径
python examples/vibe_coding_self_healing.py   # 故意喂坏数据 → 自修复 v2
python examples/vibe_coding_workflow.py       # 一句话生成 + 执行整个工作流
python examples/vibe_coding_audit_rollback.py # 审计 + 一键回滚
python examples/vibe_coding_brief_first.py    # 双轮 vs 单轮生成
```

---

## 与 Trae 等 IDE 的差异化

| 能力 | Trae | vibe_coding |
| --- | --- | --- |
| 生成时沙箱试跑 test_cases | × | ✓（CodeSandbox 子进程隔离） |
| 运行时失败自动修复 + 固化新版本 | × | ✓（CodeSkillRuntime） |
| AST 严格白名单 + 禁用内置函数 | 弱 | ✓（CodeValidator） |
| 多版本固化 + 一键回滚 | × | ✓（PatchLedger） |
| 配置层 + 代码层共存 | × | ✓ |
| 一句话生成含多 Skill 的完整工作流 | 局部 | ✓（NLWorkflowFactory） |
| 领域守卫 | × | ✓（沿用 CodeSkillRuntime） |
| Patch 审计 + 进化轨迹 | × | ✓ |
| Brief-First 双轮生成 | × | ✓ |

---

## 测试

```bash
pytest tests/test_vibe_coding_*.py
```

47 个测试全部 MockLLM 离线可跑，覆盖：

- LLM 客户端抽象 + MockLLM
- Code Factory 闭环（happy path / 自动修复 / 重试耗尽 / 安全校验）
- Config Factory 落库与运行
- Workflow Factory 多节点生成 + 校验失败检测
- Workflow Engine 顺序执行 + 点号路径取参
- PatchLedger 历史 / 回滚 / 报告
- VibeCoder 一站式 + CLI 子命令

---

## 后期升级方式

1. 改 `eskill.vibe_coding.*` 任意模块
2. 跑 `pytest tests/test_vibe_coding_*.py` 保证回归
3. 在 `vibe-coding/scripts/sync_from_eskill.py` 同步到独立包
4. bump 独立包 `_version.py`

主版本与独立版本永远保持同一份语义；独立版仅 import 路径不同。
