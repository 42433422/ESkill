# 变更日志

所有重要变更将记录在此文件中。

## [Unreleased]

### 新增
- 完整的技能生命周期：静态执行 → 动态触发 → 补丁生成 → 固化新版本
- ESkillWrapper 封装层：任何普通 Skill + Wrapper = 自修复 ESkill
- SkillAdapter 协议：FunctionSkillAdapter / DictSkillAdapter
- LLMPatchGenerator 接口：支持 OpenAI 兼容的 LLM 补丁生成
- 运行时观测：metrics.py 收集运行统计、失败率、质量分
- 技能发现：discovery.py 支持搜索、按领域过滤、技能索引
- SkillCreator：声明式技能创建 + normalize_id + validate_logic
- SkillBlueprint：LLM 技能设计（自然语言→Blueprint）
- Pipeline 多步执行：支持模板渲染、值设置、工具调用
- 质量门控：5 维度评估（min_length, required_keys, contains_all, contains_any, min_score）
- 自动回滚：动态执行失败时回退到上一稳定版本
- 领域守卫：动态阶段检查输入是否仍在技能领域内
- 历史学习：从历史成功运行中检索相似输入，复用补丁
- SkillTestSuite：单元测试框架
- SkillPackageManager：包管理与验证
- AdaptivePolicyEngine：策略引擎
- CrystalLibrary：技能结晶库
- LayeredMemoryStore：分层记忆存储
- AuditTrail：审计日志
- SkillHealthChecker：健康检查
- StrategyPreset：策略预设

### 架构
- 模块化设计：22 个核心模块，职责清晰
- 零外部依赖核心（LLM 功能可选）
- Python 3.10+，现代类型标注
