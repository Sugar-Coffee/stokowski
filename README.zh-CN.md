# Stokowski 中文说明

Stokowski 是一个基于 Linear 的自动化编程调度器。它会轮询 Linear 中的 issue，把 issue 分配给 Claude Code CLI（或其他 runner），在独立工作区中执行，并根据配置的工作流状态推进 issue。

## 这个项目做什么

- 从 Linear 拉取处于指定状态的 issue
- 为每个 issue 创建独立工作区（git clone）
- 启动 Claude Code CLI 执行调查/实现/审查等阶段
- 通过 Linear 状态机驱动“调查 → 审核 → 实现 → 审核 → 合并”等流程
- 把运行过程与结果写回 Linear（评论与状态）

## 如何在 Linear 里发 issue 驱动 Claude Code CLI

1. 在 Linear 中创建 issue，建议标题形如：`REL-123: 简短标题`。
2. 按下方“推荐格式”填写描述，特别是清晰的验收标准。
3. 确认该 issue 所属项目/团队与 `workflow.yaml` 的 `tracker.project_slug` 和 `linear_states` 对应。
4. 将 issue 移动到 `Todo`（或你在 `workflow.yaml` 里配置的 `linear_states.todo`）。
5. Stokowski 检测到后会自动把 issue 移到 `In Progress` 并开始执行。

## 推荐的 issue 描述格式

建议在 Linear 里使用以下 Markdown 结构。该格式兼顾可读性与可执行性，便于 Claude Code 抽取需求并推进流程。

```md
## Summary
一句话说明要做什么。

## Context
为什么要做这件事？背景是什么？

## Scope
**In scope:**
- 列出需要完成的内容（逐条、可验证）

**Out of scope:**
- 明确不做的内容

## Implementation Notes
- 关键文件/模块
- 现有模式或约束（例如：必须遵循的架构、风格）
- 依赖项或前置条件

## Acceptance Criteria
```json
{
  "criteria": [
    { "description": "X 功能按预期工作", "verified": false },
    { "description": "类型检查通过", "verified": false },
    { "description": "所有测试通过", "verified": false }
  ]
}
```
