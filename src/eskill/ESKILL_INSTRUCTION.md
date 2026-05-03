# ESkill 自修复说明文档

## 概述

这是一个 **ESkill（Evolvable Dynamic Static Skill）** 技能，具有**自修复能力**。

## 核心特性

### 1. 自修复生命周期

```
普通执行 ──► 失败/质量不达标 ──► 动态适配 ──► 修复执行 ──► 固化新版本
   │                │                    │              │
   ▼                ▼                    ▼              ▼
 版本1稳定运行   进入动态模式        生成修复补丁      新版本自动保存
```

### 2. 工作模式

| 模式 | 说明 | 适用场景 |
|------|------|---------|
| **无 LLM 模式** | 使用内置规则引擎进行修复 | 测试环境、降级场景 |
| **LLM 模式** | 调用大模型生成智能修复方案 | 生产环境、复杂场景 |

### 3. 对外接口

ESkill 对外暴露的接口与普通 Skill **完全一致**：

```python
# 调用方式不变
result = skill.execute(input_data)

# 但内部会自动处理：
# - 失败检测
# - 质量评估
# - 动态修复
# - 版本固化
```

## AI 使用指南

### 当你是 LLM Patch Generator 时

如果此技能配置了 LLM 补丁生成器，你需要在修复时生成如下格式的补丁：

```json
{
  "template": "修复后的模板内容",
  "required_fields": ["字段1", "字段2"],
  "type": "template_transform 或 pipeline",
  "steps": [
    {
      "id": "step1",
      "type": "template_transform",
      "template": "步骤1模板",
      "output_var": "result1"
    }
  ],
  "metadata": {
    "修复说明": "简要说明修复内容"
  }
}
```

### 修复原则

1. **保持领域一致性**：修复后的逻辑仍应在原技能领域内
2. **利用历史信息**：参考历史成功修复记录
3. **渐进式修复**：优先小调整，避免大改动
4. **质量优先**：确保修复后通过质量门控

## 质量门控维度

| 维度 | 说明 |
|------|------|
| `min_length` | 输出最小长度 |
| `required_keys` | 必须包含的输出字段 |
| `contains_all` | 必须包含的文本片段 |
| `contains_any` | 至少包含一个的文本片段 |
| `min_score` | 最低质量分数（0-1） |

## 版本管理

- 每次成功修复会自动固化为新版本
- 修复失败会自动回滚到上一稳定版本
- 超出技能领域的请求会被直接拒绝

## 典型使用场景

### 场景1：模板技能修复

```python
# 原始技能：简单模板
template = "产品简介：${name}"

# 失败原因：输出太短，质量不达标
# 自动修复：加入更多字段
template = "产品简介：${name}。\n特点：${features}\n价格：${price}"
```

### 场景2：函数技能修复

```python
# 原始技能：处理数据
def process_data(input_data):
    return {"result": input_data["data"].upper()}

# 失败原因：缺少 data 字段
# 自动修复：调整字段要求或添加默认值
def process_data(input_data):
    data = input_data.get("data", input_data.get("text", ""))
    return {"result": data.upper()}
```

## 注意事项

1. **ESkill 不是万能药**：只修复技能内部问题，不解决外部环境问题
2. **修复有边界**：不能改变技能的核心功能和领域
3. **版本会累积**：定期清理无用版本，避免存储膨胀
4. **LLM 不是必须的**：没有 LLM 时仍能工作，只是修复能力有限
