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

## ✨ Highlights

<div align="center">

<img src="docs/images/benchmark_metrics_en.svg" alt="PowerMem LOCOMO Benchmark Metrics" width="900"/>

</div>

- 🎯 **Accurate**: **[48.77% Accuracy Improvement]** More accurate than full-context in the LOCOMO benchmark (78.70% VS 52.9%)
- ⚡ **Agile**: **[91.83% Faster Response]** Significantly reduced p95 latency for retrieval compared to full-context (1.44s VS 17.12s)
- 💰 **Affordable**: **[96.53% Token Reduction]** Significantly reduced costs compared to full-context without sacrificing performance (0.9k VS 26k)

# 🧠 PowerMem - Intelligent Memory System

In AI application development, enabling large language models to persistently "remember" historical conversations, user preferences, and contextual information is a core challenge. PowerMem combines a hybrid storage architecture of vector retrieval, full-text search, and graph databases, and introduces the Ebbinghaus forgetting curve theory from cognitive science to build a powerful memory infrastructure for AI applications. The system also provides comprehensive multi-agent support capabilities, including agent memory isolation, cross-agent collaboration and sharing, fine-grained permission control, and privacy protection mechanisms, enabling multiple AI agents to achieve efficient collaboration while maintaining independent memory spaces.

## 🚀 Core Features

### 👨‍💻 Developer Friendly
- 🔌 **[Lightweight Integration](docs/examples/scenario_1_basic_usage.md)**: Provides a simple Python SDK, automatically loads configuration from `.env` files, enabling developers to quickly integrate into existing projects

### 🧠 Intelligent Memory Management
- 🔍 **[Intelligent Memory Extraction](docs/examples/scenario_2_intelligent_memory.md)**: Automatically extracts key facts from conversations through LLM, intelligently detects duplicates, updates conflicting information, and merges related memories to ensure accuracy and consistency of the memory database
- 📉 **[Ebbinghaus Forgetting Curve](docs/examples/scenario_8_ebbinghaus_forgetting_curve.md)**: Based on the memory forgetting patterns from cognitive science, automatically calculates memory retention rates and implements time-decay weighting, prioritizing recent and relevant memories, allowing AI systems to naturally "forget" outdated information like humans

### 🤖 Multi-Agent Support
- 🔐 **[Agent Shared/Isolated Memory](docs/examples/scenario_3_multi_agent.md)**: Provides independent memory spaces for each agent, supports cross-agent memory sharing and collaboration, and enables flexible permission management through scope control

### 🎨 Multimodal Support
- 🖼️ **[Text, Image, and Audio Memory](docs/examples/scenario_7_multimodal.md)**: Automatically converts images and audio to text descriptions for storage, supports retrieval of multimodal mixed content (text + image + audio), enabling AI systems to understand richer contextual information

### 💾 Deeply Optimized Data Storage
- 📦 **[Sub Stores Support](docs/examples/scenario_6_sub_stores.md)**: Implements data partition management through sub stores, supports automatic query routing, significantly improving query performance and resource utilization for ultra-large-scale data
- 🔗 **[Hybrid Retrieval](docs/examples/scenario_2_intelligent_memory.md)**: Combines multi-channel recall capabilities of vector retrieval, full-text search, and graph retrieval, builds knowledge graphs through LLM and supports multi-hop graph traversal for precise retrieval of complex memory relationships

## 🚀 Quick Start

### 📥 Installation

```bash
pip install powermem
```

### 💡 Basic Usage

**✨ Simplest Way**: Create memory from `.env` file automatically! [Configuration Reference](.env.example)

```python
from powermem import Memory, auto_config

# Load configuration (auto-loads from .env)
config = auto_config()
# Create memory instance
memory = Memory(config=config)

# Add memory
memory.add("User likes coffee", user_id="user123")

# Search memories
results = memory.search("user preferences", user_id="user123")
for result in results.get('results', []):
    print(f"- {result.get('memory')}")
```

For more detailed examples and usage patterns, see the [Getting Started Guide](docs/guides/0001-getting_started.md).

## 🔗 Integrations & Demos

- 🔗 **LangChain Integration**: Build medical support chatbot using LangChain + PowerMem + OceanBase, [View Example](examples/langchain/README.md)
- 🔗 **LangGraph Integration**: Build customer service chatbot using LangGraph + PowerMem + OceanBase, [View Example](examples/langgraph/README.md)

## 📚 Documentation

- 📖 **[Getting Started](docs/guides/0001-getting_started.md)**: Installation and quick start guide
- ⚙️ **[Configuration Guide](docs/guides/0003-configuration.md)**: Complete configuration options
- 🤖 **[Multi-Agent Guide](docs/guides/0005-multi_agent.md)**: Multi-agent scenarios and examples
- 🔌 **[Integrations Guide](docs/guides/0009-integrations.md)**: Integrations Guide
- 📦 **[Sub Stores Guide](docs/guides/0006-sub_stores.md)**: Sub stores usage and examples
- 📋 **[API Documentation](docs/api/overview.md)**: Complete API reference
- 🏗️ **[Architecture Guide](docs/architecture/overview.md)**: System architecture and design
- 📓 **[Examples](docs/examples/overview.md)**: Interactive Jupyter notebooks and use cases
- 👨‍💻 **[Development Documentation](docs/development/overview.md)**: Developer documentation

## 💬 Support

- 🐛 **Issue Reporting**: [GitHub Issues](https://github.com/oceanbase/powermem/issues)
- 💭 **Discussions**: [GitHub Discussions](https://github.com/oceanbase/powermem/discussions)

---

## 📄 License

This project is licensed under the Apache License 2.0 - see the [LICENSE](LICENSE) file for details.