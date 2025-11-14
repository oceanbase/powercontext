<p align="center">
    <a href="https://github.com/oceanbase/oceanbase">
        <img alt="OceanBase Logo" src="docs/images/oceanbase_Logo.png" width="50%" />
    </a>
</p>

<p align="center">
    <a href="https://pepy.tech/project/powermem">
        <img src="https://img.shields.io/pypi/dm/powermem" alt="PowerMem PyPI - Downloads">
    </a>
    <a href="https://github.com/oceanbase/powermem">
        <img src="https://img.shields.io/github/commit-activity/m/oceanbase/powermem?style=flat-square" alt="GitHub commit activity">
    </a>
    <a href="https://pypi.org/project/powermem" target="blank">
        <img src="https://img.shields.io/pypi/v/powermem?color=%2334D058&label=pypi%20package" alt="Package version">
    </a>
    <a href="https://github.com/oceanbase/powermem/blob/master/LICENSE">
        <img alt="license" src="https://img.shields.io/badge/license-Apache%202.0-green.svg" />
    </a>
    <a href="https://img.shields.io/badge/python%20-3.10.0%2B-blue.svg">
        <img alt="pyversions" src="https://img.shields.io/badge/python%20-3.10.0%2B-blue.svg" />
    </a>
    <a href="https://deepwiki.com/oceanbase/powermem">
        <img alt="Ask DeepWiki" src="https://deepwiki.com/badge.svg" />
    </a>
    <a href="https://discord.com/invite/74cF8vbNEs">
        <img src="https://img.shields.io/badge/Discord-Join%20Discord-5865F2?logo=discord&logoColor=white" alt="Join Discord">
    </a>
</p>

[English](README.md) | [中文](README_CN.md) | [日本語](README_JP.md)

## ✨ 亮点

<div align="center">

<img src="docs/images/benchmark_metrics_cn.svg" alt="PowerMem LOCOMO 压测指标" width="900"/>

</div>

- 🎯 **更准**：**[准确率提升 48.77%]** 在 LOCOMO 基准测试中，相比于 full-context 更准确（78.70% VS 52.9%）
- ⚡ **更快**：**[响应速度快 91.83%]** 相比于 full-context，检索的 p95 延迟显著降低（1.44s VS 17.12s）
- 💰 **更省**：**[Token 用量降低 96.53%]** 相比于full-context，在不牺牲性能的前提下显著降低成本（0.9k VS 26k）

# 🧠 PowerMem - 智能AI记忆系统

在 AI 应用开发中，如何让大语言模型持久化地"记住"历史对话、用户偏好和上下文信息是一个核心挑战。PowerMem 融合向量检索、全文检索和图数据库的混合存储架构，并引入认知科学的艾宾浩斯遗忘曲线理论，为 AI 应用构建了强大的记忆基础设施。系统还提供完善的多智能体支持能力，包括智能体记忆隔离、跨智能体协作共享、细粒度权限控制和隐私保护机制，让多个 AI 智能体能够在保持独立记忆空间的同时实现高效协作。

## 🚀 核心特性

### 👨‍💻 开发者友好
- 🔌 **[轻量级接入方式](docs/examples/scenario_1_basic_usage.md)**：提供简洁的 Python SDK 支持，自动从 `.env` 文件加载配置，让开发者快速集成到现有项目中

### 🧠 智能记忆管理
- 🔍 **[记忆的智能提取](docs/examples/scenario_2_intelligent_memory.md)**：通过 LLM 自动从对话中提取关键事实，智能检测重复、更新冲突信息并合并相关记忆，确保记忆库的准确性和一致性
- 📉 **[艾宾浩斯遗忘曲线](docs/examples/scenario_8_ebbinghaus_forgetting_curve.md)**：基于认知科学的记忆遗忘规律，自动计算记忆保留率并实现时间衰减加权，优先返回最近且相关的记忆，让 AI 系统像人类一样自然"遗忘"过时信息

### 🤖 多智能体支持
- 🔐 **[智能体共享/隔离记忆](docs/examples/scenario_3_multi_agent.md)**：为每个智能体提供独立的记忆空间，支持跨智能体记忆共享和协作，通过作用域控制实现灵活的权限管理

### 🎨 多模态支持
- 🖼️ **[文本、图像、语音记忆](docs/examples/scenario_7_multimodal.md)**：自动将图像和音频转换为文本描述并存储，支持多模态混合内容（文本+图像+音频）的检索，让 AI 系统理解更丰富的上下文信息

### 💾 深度优化数据存储
- 📦 **[支持子存储（Sub Stores）](docs/examples/scenario_6_sub_stores.md)**：通过子存储实现数据
的分区管理，支持自动路由查询，显著提升超大规模数据的查询性能和资源利用率
- 🔗 **[混合检索](docs/examples/scenario_2_intelligent_memory.md)**：融合向量检索、全文搜索和图检索的多路召回能力，通过 LLM 构建知识图谱并支持多跳图遍历，精准检索复杂的记忆关联关系

## 🚀 快速开始

### 📥 安装

```bash
pip install powermem
```

### 💡 基本使用

**✨ 最简单的方式**：从 `.env` 文件读取配置自动创建记忆！[配置文件参考](configs/env.example)

```python
from powermem import Memory, auto_config

# 自动从 .env 加载配置并初始化
config = auto_config()
memory = Memory(config=config)

# 添加记忆
memory.add("用户喜欢咖啡", user_id="user123")

# 搜索记忆
memories = memory.search("用户偏好", user_id="user123")
for memory in memories:
    print(f"- {memory.get('memory')}")
```

更多详细示例和使用模式，请参阅[入门指南](docs/guides/0001-getting_started.md)。

## 🔗 集成与演示

- 🔗 **LangChain 集成**：基于 LangChain + PowerMem + OceanBase 构建医疗支持机器人，[查看示例](examples/langchain/README.md)
- 🔗 **LangGraph 集成**：基于 LangGraph + PowerMem + OceanBase 构建客户服务机器人，[查看示例](examples/langgraph/README.md)

## 📚 文档

- 📖 **[入门指南](docs/guides/0001-getting_started.md)**：安装和快速开始指南
- ⚙️ **[配置指南](docs/guides/0003-configuration.md)**：完整的配置选项
- 🤖 **[多智能体指南](docs/guides/0005-multi_agent.md)**：多智能体场景和示例
- 🔌 **[集成指南](docs/guides/0009-integrations.md)**：集成指南
- 📦 **[子存储指南](docs/guides/0006-sub_stores.md)**：子存储的使用方法和示例
- 📋 **[API 文档](docs/api/overview.md)**：完整的 API 参考
- 🏗️ **[架构指南](docs/architecture/overview.md)**：系统架构和设计
- 📓 **[示例](docs/examples/overview.md)**：交互式 Jupyter 笔记本和使用案例
- 👨‍💻 **[开发者文档](docs/development/overview.md)**：开发者文档

## 💬 支持

- 🐛 **问题反馈**：[GitHub Issues](https://github.com/oceanbase/powermem/issues)
- 💭 **讨论交流**：[GitHub Discussions](https://github.com/oceanbase/powermem/discussions)

---

## 📄 许可证

本项目采用 Apache License 2.0 许可证 - 详情请参阅 [LICENSE](LICENSE) 文件。