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

# PowerMem — AI アプリ向けインテリジェントメモリ

PowerMem は AI アプリケーション向けの永続メモリ層です。ベクトル・全文・グラフ検索に加え、LLM によるメモリ抽出と時間減衰（エビングハウス型）、マルチエージェントの分離と協調、ユーザープロフィール、テキスト・画像・音声などのマルチモーダル手がかりを扱います。

Python SDK、CLI（`pmem`）、HTTP API Server（Dashboard 付き）、MCP Server は同一の `.env` を共有します。設定は [.env.example](.env.example) と [設定ガイド](docs/guides/0003-configuration.md) を参照してください。

> **ニュース:** [OpenClaw](https://github.com/openclaw-ai/openclaw) はプラグイン [memory-powermem](https://github.com/ob-labs/memory-powermem) により PowerMem を長期メモリとして利用できます（`openclaw plugins install memory-powermem`）。

## ベンチマーク（LOCOMO）

<div align="center">

<img src="docs/images/benchmark_metrics_jp.svg" alt="PowerMem LOCOMO ベンチマーク指標" width="900"/>

</div>

会話全文をコンテキストに載せる方式との比較（[LOCOMO](https://github.com/snap-research/locomo)）：

| 観点 | 結果 |
|------|------|
| 精度 | 78.70% vs. 52.9% |
| 検索 p95 遅延 | 1.44s vs. 17.12s |
| トークン | 約 0.9k vs. 26k |

## 機能概要

**インターフェースとツール** — [Python 統合](docs/examples/scenario_1_basic_usage.md)；[CLI](docs/guides/0012-cli_usage.md)（`pmem`）；[HTTP API / Dashboard](docs/api/0005-api_server.md)；[MCP](docs/api/0004-mcp.md)。

**メモリパイプラインと検索** — [スマート抽出と更新](docs/examples/scenario_2_intelligent_memory.md)；[エビングハウス型減衰](docs/examples/scenario_8_ebbinghaus_forgetting_curve.md)；[ハイブリッド検索（ベクトル / 全文 / グラフ）](docs/examples/scenario_2_intelligent_memory.md)；[サブストアとルーティング](docs/examples/scenario_6_sub_stores.md)。

**プロフィールとマルチエージェント** — [ユーザープロフィール](docs/examples/scenario_9_user_memory.md)；[共有 / 分離メモリとスコープ](docs/examples/scenario_3_multi_agent.md)。

**マルチモーダル** — [テキスト・画像・音声](docs/examples/scenario_7_multimodal.md)。

## クイックスタート

### 1. インストール

```bash
pip install powermem
```

### 2. SDK サンプル

```python
from powermem import Memory, auto_config

config = auto_config()
memory = Memory(config=config)

memory.add("ユーザーはコーヒーが好き", user_id="user123")

results = memory.search("ユーザー設定", user_id="user123")
for result in results.get("results", []):
    print(f"- {result.get('memory')}")
```

詳細は [はじめに](docs/guides/0001-getting_started.md) を参照してください。

### 3. その他の利用形態

| 形態 | 代表的なコマンド | ドキュメント |
|------|------------------|--------------|
| CLI | `pmem memory add` / `pmem memory search`；`pmem shell` | [CLI 使用ガイド](docs/guides/0012-cli_usage.md) |
| HTTP + Dashboard | `powermem-server --host 0.0.0.0 --port 8000`；イメージ `oceanbase/powermem-server:latest`；`docker-compose -f docker/docker-compose.yml` | [API Server](docs/api/0005-api_server.md) |
| MCP | `uvx powermem-mcp sse`（stdio / streamable-http も可）；`powermem` と `uv` が必要 | [MCP Server](docs/api/0004-mcp.md) |

## ドキュメントとサンプル

| 種類 | リンク |
|------|--------|
| はじめに | [Guide](docs/guides/0001-getting_started.md) |
| 設定 | [Configuration](docs/guides/0003-configuration.md) |
| CLI | [CLI](docs/guides/0012-cli_usage.md) |
| マルチエージェント | [Multi-Agent](docs/guides/0005-multi_agent.md) |
| 統合 | [Integrations](docs/guides/0009-integrations.md) |
| サブストア | [Sub stores](docs/guides/0006-sub_stores.md) |
| API | [API overview](docs/api/overview.md) |
| アーキテクチャ | [Architecture](docs/architecture/overview.md) |
| シナリオ例 | [Examples](docs/examples/overview.md) |
| 開発 | [Development](docs/development/overview.md) |
| サンプル：LangChain | [医療サポート](examples/langchain/README.md) |
| サンプル：LangGraph | [カスタマーサービス](examples/langgraph/README.md) |

## リリースハイライト

| バージョン | 日付 | 内容 |
|------------|------|------|
| 1.0.0 | 2026-03-16 | CLI（`pmem`）：メモリ操作、設定、バックアップ/復元/マイグレーション、対話シェル、補完；Web Dashboard |
| 0.5.0 | 2026-02-06 | SDK/API 設定の統一（pydantic-settings）；OceanBase native hybrid search；Memory クエリと一覧ソート；プロフィールの言語カスタマイズ |
| 0.4.0 | 2026-01-20 | スパースベクトル混合検索；プロフィール起点のクエリ書き換え；スキーマ更新と移行ツール |
| 0.3.0 | 2026-01-09 | 本番向け HTTP API Server；Docker |
| 0.2.0 | 2025-12-16 | プロフィール強化；マルチモーダル（テキスト/画像/音声） |
| 0.1.0 | 2025-11-14 | コアメモリとハイブリッド検索；LLM 抽出；忘却曲線；マルチエージェント；OceanBase/PostgreSQL/SQLite；グラフ検索 |

## サポート

- **Issues**：[GitHub Issues](https://github.com/oceanbase/powermem/issues)
- **ディスカッション**：[GitHub Discussions](https://github.com/oceanbase/powermem/discussions)

---

## ライセンス

Apache License 2.0 — 詳細は [LICENSE](LICENSE)。
</think>
修正日文 README 中的错误链接 `0012-web_usage` → `0012-cli_usage`。

<｜tool▁calls▁begin｜><｜tool▁call▁begin｜>
StrReplace