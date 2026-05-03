# ESkill 双层进化架构

## 核心思想

```
AI Employee = 壳（容器）
Workflow Node = Skill（真正的技能）
```

双层架构让**员工会升级**，**技能也会升级**，两层互相影响、协同进化。

```
┌─────────────────────────────────────────────────────────────┐
│  Layer 2: Employee 层（壳）                                  │
│  ESkillEmployeeWrapper — 员工容器的自修复进化能力              │
│                                                             │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌────────┐│
│  │ perception  │ │   memory    │ │  cognition  │ │actions ││
│  │  感知层进化  │ │  记忆层进化  │ │  认知层进化  │ │行动层进化│
│  │ 输入格式适配 │ │ 检索策略调整 │ │ Prompt进化  │ │路由优化 ││
│  └─────────────┘ └─────────────┘ └─────────────┘ └────────┘│
│                          ↕ 升级传播                          │
└─────────────────────────────────────────────────────────────┘
                              │
                    DualLayerBridge
                    （升级传播 + 策略同步）
                              │
┌─────────────────────────────────────────────────────────────┐
│  Layer 1: Skill 层（工作流节点）                               │
│  ESkillNodeWrapper — 节点的自修复进化能力                      │
│                                                             │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐       │
│  │  greet   │ │  query   │ │ recommend│ │  notify  │       │
│  │  问候节点 │ │ 查询节点  │ │ 推荐节点  │ │ 通知节点  │       │
│  │ 自修复    │ │ 自修复    │ │ 自修复    │ │ 自修复    │       │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘       │
│                                                             │
│  静态执行 → 质量门控 → 动态适配 → 固化新版本                   │
└─────────────────────────────────────────────────────────────┘
```

## 两层职责

### Skill 层（Layer 1）

- **定位**：工作流中的具体节点，真正干活的技能
- **自修复对象**：节点执行失败、输出质量不达标、API 超时等
- **进化方式**：静态执行 → 质量检查 → 规则/LLM 修复 → 重试 → 固化新版本
- **升级传播**：节点修复固化后，通知 Employee 层调整策略

### Employee 层（Layer 2）

- **定位**：AI 员工容器，管理多个 Skill 的协调
- **自修复对象**：perception/memory/cognition/actions 任何一层失败
- **进化方式**：层执行失败 → 规则/LLM 修复 → 调整层参数 → 重试
- **策略同步**：Employee 进化后，影响旗下所有 Skill 的执行策略

## 升级传播机制

### 方向 1：Skill → Employee

```
Skill 节点修复固化新版本
    ↓
触发 on_solidified 回调
    ↓
DualLayerBridge 接收事件
    ↓
通知关联的 Employee
    ↓
Employee._on_skill_solidified()
    ↓
Employee 调整执行策略（如：更信任新版本的 Skill）
```

### 方向 2：Employee → Skill

```
Employee 某层进化（如 cognition 层 Prompt 优化）
    ↓
Employee 层保存新配置
    ↓
sync_employee_to_skills()
    ↓
调整旗下 Skill 节点的质量门控/触发策略
```

## 核心类

### Employee 层

| 类 | 职责 |
|---|---|
| `ESkillEmployeeWrapper` | 员工容器自修复主类 |
| `EmployeeLayerConfig` | 四层架构的自修复配置 |
| `EmployeeLayerRunResult` | 员工层执行结果 |

### Skill 层

| 类 | 职责 |
|---|---|
| `ESkillNodeWrapper` | 工作流节点自修复主类 |
| `SkillNodeConfig` | 节点配置（质量门控/降级策略/重试） |
| `SkillNodeRunResult` | 节点执行结果 |
| `WorkflowESkillEngine` | 管理所有节点的引擎 |

### 桥接层

| 类 | 职责 |
|---|---|
| `DualLayerBridge` | 连接两层，处理升级传播 |
| `DualLayerOrchestrator` | 高级封装，一键管理 Employee + Skill |
| `UpgradeEvent` | 层间升级事件 |

## 使用示例

### 基础用法：双层编排器

```python
from eskill import DualLayerOrchestrator, EmployeeLayerConfig, JsonSkillStore

store = JsonSkillStore("data.json")
orchestrator = DualLayerOrchestrator(store)

# 定义一个带自修复能力的员工
emp = orchestrator.define_employee(
    employee_id="sales_assistant",
    layer_config=EmployeeLayerConfig(
        perception_enabled=True,
        cognition_enabled=True,
    ),
    skills=[
        {"node_id": "greet", "type": "eskill", "execute": greet_fn},
        {"node_id": "query", "type": "openapi", "execute": query_fn},
    ]
)

# 执行 Skill 节点
result = orchestrator.run_skill_node("greet", {"name": "张三"})

# 执行 Employee 任务
result = orchestrator.run(
    employee_id="sales_assistant",
    task="推荐一款手机",
    perception_fn=my_perception,
    cognition_fn=my_cognition,
    actions_fn=my_actions,
)

# 获取双层健康报告
report = orchestrator.get_report()
```

### 进阶用法：直接操作两层

```python
from eskill import (
    DualLayerBridge,
    ESkillEmployeeWrapper,
    EmployeeLayerConfig,
    ESkillNodeWrapper,
    SkillNodeConfig,
)

bridge = DualLayerBridge(store, llm_generator)

# 创建 Employee
employee = bridge.create_employee("emp_1", EmployeeLayerConfig())

# 注册 Skill 节点（自动关联到 Employee）
node_config = SkillNodeConfig(
    node_type="openapi",
    node_id="query_api",
    quality_gate={"required_keys": ["data"]},
    fallback_strategy="default",
    retry_count=2,
)
bridge.register_skill_node("query_api", node_config, execute_fn, employee_id="emp_1")

# 执行
result = bridge.execute_skill_node("query_api", {"query": "phone"})
```

## 与 MODstore 集成

MODstore 工作流引擎已有 `eskill` 节点类型。双层架构可以这样集成：

```python
# MODstore 工作流引擎中的 _execute_eskill_node 方法
# 可以替换为使用 ESkillNodeWrapper

from eskill import ESkillNodeWrapper, SkillNodeConfig

def _execute_eskill_node(self, node, data, config, *, session, workflow_id, user_id=0):
    node_config = SkillNodeConfig(
        node_type="eskill",
        node_id=str(node.id),
        node_name=node.name,
        quality_gate=config.get("quality_gate", {}),
        trigger_policy=config.get("trigger_policy", {}),
        retry_count=config.get("retry_count", 0),
    )

    wrapper = ESkillNodeWrapper(
        node_config=node_config,
        store=self.eskill_store,
        llm_generator=self.llm_generator,
    )

    def execute_fn(input_data):
        # 原有的 ESkill 执行逻辑
        return default_eskill_runtime.run(...)

    result = wrapper.execute(execute_fn, input_data)
    return result.output
```

## 进化路径

```
初始状态
    │
    ▼
┌─────────────────┐
│  Skill 层执行失败 │
│  触发规则修复     │
└─────────────────┘
    │
    ▼
┌─────────────────┐
│  修复成功，固化   │
│  新版本 v2       │
└─────────────────┘
    │
    ▼
┌─────────────────┐
│  通知 Employee 层 │
│  调整信任策略    │
└─────────────────┘
    │
    ▼
┌─────────────────┐
│  Employee 认知层 │
│  优化 Prompt    │
└─────────────────┘
    │
    ▼
┌─────────────────┐
│  影响所有 Skill  │
│  执行参数调整    │
└─────────────────┘
    │
    ▼
  持续进化...
```

## 无 LLM 降级

双层架构都支持无 LLM 模式：

- **有 LLM**：规则修复 → LLM 修复 → 固化
- **无 LLM**：规则修复 → 降级策略 → 记录失败

Employee 层和 Skill 层各自独立降级，互不影响。
