# 实验：Agent 接力——聊天清空后还能继续吗？

Workshop，使用 PowerContext，接力完成一份三日旅行方案，体验跨会话、跨 Agent 的工作接续。

**[打开 agent-relay.ipynb](agent-relay.ipynb)。只需要这一份 Notebook。**

- 准备环境：安装固定的 PowerContext 1.0.0 包，在 Notebook 中填写模型地址、名称和 API Key，启动本地 Server。
- 先体验：第一棒做一半，清空聊天做对照，恢复后修改要求，换 Agent 完成剩余工作。
- 边做边理解：通过文字讲解和对照表，了解 Memory / Source / Handoff、要求修订与交接恢复。
- 拆开看：直接读取 Memory 和 Handoff，把实际记录与保存、恢复的代码对应起来。
- 经验与 Skill（可选）：填写实际观察，提交并审核 Experience，再整理和审核 Skill，让新会话读取后修改旅行方案。

完整代码、教学讲解和操作步骤都在 Notebook 中，上传这一份文件即可，无需额外配置文件或终端操作。
