# 示例 {#examples}

本节包含以交互式 Jupyter Notebook 格式编写的代码和解释的分步示例。

## 可用场景 {#available-scenarios}

每个场景均提供以下两种形式：
- **Jupyter Notebook** (`.ipynb`) - 交互式、可运行的示例
- **Markdown** (`.md`) - 文档格式

### Markdown 文档 {#markdown-documentation}

 - [场景 1: 基本用法](./scenario_1_basic_usage.md)
 - [场景 2: 智能记忆](./scenario_2_intelligent_memory.md)
 - [场景 3: Multi-Agent](./scenario_3_multi_agent.md)
 - [场景 4: 异步操作](./scenario_4_async_operations.md)
 - [场景 5: 自定义集成](./scenario_5_custom_integration.md)
 - [场景 6: Sub Stores](./scenario_6_sub_stores.md)
 - [场景 7: 多模态能力](./scenario_7_multimodal.md)
 - [场景 8: 艾宾浩斯遗忘曲线](./scenario_8_ebbinghaus_forgetting_curve.md)
 - [场景 9: 用户档案管理](./scenario_9_user_memory.md)
 - [场景 10: Sparse Vector](./scenario_10_sparse_vector.md)

## 快速开始 {#quick-start}

### 使用 Jupyter Notebooks {#using-jupyter-notebooks}

1. **安装 Jupyter**：
   ```bash
   pip install jupyter notebook
   ```
2. **启动 Jupyter Notebook**：
   ```bash
   jupyter notebook
   ```
3. **打开一个场景 Notebook**：
   - 如果你是 PowerMem 新手，可以从 `scenario_1_basic_usage.ipynb` 开始
   - 使用 Shift+Enter 顺序运行每个单元格
   - 修改代码并进行实验！

### 使用 Python 脚本 {#using-python-scripts}

你也可以通过从 Markdown 文件中复制代码，将示例作为 Python 脚本运行。

## Notebook 功能 {#notebook-features}

每个 Notebook 包括：
- **逐步说明** - 每一步都有清晰的解释
- **可运行的代码单元格** - 可以直接在 Notebook 中执行代码
- **Markdown 文档** - 提供解释和上下文
- **扩展练习** - 自主尝试的练习题

## 推荐学习路径 {#recommended-learning-path}

1. **从场景 1 开始** - 学习基本的记忆操作
2. **探索场景 2** - 理解智能记忆功能
3. **尝试场景 3** - 处理Multi-Agent 场景
4. **查看场景 4** - 学习异步操作
5. **场景 5** - 为高级用户提供的自定义集成
6. **场景 6** - 子存储，用于记忆分区和优化
7. **场景 7** - 多模态功能，用于图像和多媒体处理
8. **场景 8** - 艾宾浩斯遗忘曲线，用于基于时间的记忆衰减和保留优化
9. **场景 9** - 用户档案管理，用于自动用户档案提取和与记忆搜索的集成
10. **场景 10** - 稀疏向量，通过模式升级和数据迁移提高搜索精度

## 要求 {#requirements}

- Python 3.11+
- 已安装 powermem (`pip install powermem`)
- Jupyter Notebook（用于交互式 Notebook）
- 已配置 LLM 提供商（用于场景 2+ 的智能功能）