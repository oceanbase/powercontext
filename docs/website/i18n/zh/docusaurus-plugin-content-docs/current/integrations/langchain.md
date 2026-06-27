# LangChain 和 LangGraph {#langchain-and-langgraph}

将 PowerMem 用作 LangChain 和 LangGraph 应用的持久记忆层。

## 前置条件 {#prerequisites}

- Python 3.11+。
- 配置了 LLM 提供商、API 密钥和模型的 PowerMem。
- 应用所需的 LangChain 或 LangGraph 依赖项。

## 安装 {#install}

对于 LangChain：
```bash
pip install powermem langchain langchain-core langchain-openai
```
对于 LangGraph：
```bash
pip install powermem langgraph langchain-core langchain-openai
```
对于可运行的医疗支持示例：
```bash
cd examples/langchain
pip install -r requirements.txt
```
## LangChain 模式 {#langchain-pattern}

PowerMem 作为持久化检索和写回层，与 LangChain 一起使用：

1. 创建一个 PowerMem 的 `Memory` 实例。
2. 在每次响应之前搜索 PowerMem，以加载相关上下文。
3. 将检索到的上下文注入到 LangChain 提示或 LCEL 链中。
4. 将用户/助手的对话内容保存回 PowerMem。

最小结构：
```python
from powermem import Memory, auto_config

memory = Memory(config=auto_config())

context = memory.search(
    query="what does the user prefer",
    user_id="user123",
    limit=5,
)

memory.add(
    "User prefers concise answers with examples.",
    user_id="user123",
)
```
要查看完整的 LCEL 实现，请参阅 [`examples/langchain/README.md`](https://github.com/oceanbase/powermem/blob/main/examples/langchain/README.md) 和 [`../examples/scenario_5_custom_integration.md`](../examples/scenario_5_custom_integration.md)。

## LangGraph 模式 {#langgraph-pattern}

PowerMem 在 LangGraph 工作流中可以作为图节点或辅助工具很好地工作：

1. 添加一个加载上下文节点，用于搜索 PowerMem。
2. 将检索到的记忆添加到图状态中。
3. 使用状态感知的上下文生成响应。
4. 在响应之后添加一个保存记忆的节点。

有关完整示例，请参阅 [`../guides/0009-integrations.md`](../guides/0009-integrations.md) 中的 LangGraph 部分。

## 验证 {#verify}

1. 为测试 `user_id` 添加一个探针记忆。
2. 在调用链或图之前搜索该探针。
3. 确认检索到的记忆包含在生成的提示/上下文中。
4. 保存一个新的交互并重新搜索它。

## 故障排除 {#troubleshooting}

- 如果搜索没有返回结果，请确认写入和搜索使用的是相同的 `user_id`。
- 如果提取未创建记忆，请检查 LLM 提供商的配置和日志。
- 如果 LangChain 导入失败，请安装 `examples/langchain/requirements.txt` 中列出的包。
- 如果 Embedding 失败，请确认您的 Embedding 提供商和维度与配置的存储后端匹配。

## 另请参阅 {#see-also}

- [`examples/langchain/README.md`](https://github.com/oceanbase/powermem/blob/main/examples/langchain/README.md)
- [`../guides/0009-integrations.md`](../guides/0009-integrations.md)
- [`../examples/scenario_5_custom_integration.md`](../examples/scenario_5_custom_integration.md)