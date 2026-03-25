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

**PowerMem** は AI アプリケーション向けの長期メモリ基盤です。**ベクトル・全文・グラフ**のハイブリッド検索、**エビングハウス型の忘却**、**LLM によるメモリ抽出**、**マルチエージェント分離/共有**、**ユーザープロフィール**、**マルチモーダル**（テキスト・画像・音声）をサポートします。同一機能を **Python SDK**、**CLI（`pmem`）**、**HTTP API Server（Dashboard 付き）**、**MCP Server** から利用でき、設定は **`.env` で統一**されます。

> **ニュース:** [OpenClaw](https://github.com/openclaw-ai/openclaw) はプラグイン [memory-powermem](https://github.com/ob-labs/memory-powermem) により PowerMem を長期メモリとして利用できます（`openclaw plugins install memory-powermem`）。

## PowerMem を選ぶ理由

<div align="center">

<img src="docs/images/benchmark_metrics_jp.svg" alt="PowerMem LOCOMO ベンチマーク指標" width="900"/>

</div>

[LOCOMO](https://github.com/snap-research/locomo) ベンチマークにおいて、会話全文をコンテキストに載せる方式と比較：

- **より正確**：精度の相対的な改善（78.70% vs. 52.9%、約 48.77%）
- **より高速**：検索 **p95** が約 **1.44s** vs. **17.12s**（約 91.83% 短縮）
- **トークン削減**：約 **0.9k** vs. **26k**（約 96.53% 削減）を性能を大きく損なわずに実現

## 主な機能

### 開発者体験

- **[軽量統合](docs/examples/scenario_1_basic_usage.md)**：`.env` を読み込む Python SDK に加え、[CLI](docs/guides/0012-cli_usage.md)（`pmem`）、[MCP Server](docs/api/0004-mcp.md)、[HTTP API Server](docs/api/0005-api_server.md)

### メモリ管理

- **[スマート抽出](docs/examples/scenario_2_intelligent_memory.md)**：LLM による事実抽出、重複排除、競合解消、マージ
- **[エビングハウス忘却曲線](docs/examples/scenario_8_ebbinghaus_forgetting_curve.md)**：時間・関連度に基づく減衰、最近・有用なメモリを優先

### ユーザープロフィール

- **[ユーザープロフィール](docs/examples/scenario_9_user_memory.md)**：履歴と行動からプロフィールを構築・更新（パーソナライズ、コンパニオン等）

### マルチエージェント

- **[共有/分離メモリ](docs/examples/scenario_3_multi_agent.md)**：エージェント単位の空間、横断協業、スコープに基づく権限

### マルチモーダル

- **[テキスト・画像・音声](docs/examples/scenario_7_multimodal.md)**：要約テキスト化して保存し、混在コンテンツを検索

### ストレージと検索

- **[サブストア](docs/examples/scenario_6_sub_stores.md)**：パーティショニングと自動ルーティング（大規模向け）
- **[ハイブリッド検索](docs/examples/scenario_2_intelligent_memory.md)**：ベクトル + 全文 + グラフ（マルチホップ）、LLM 補助のグラフ構築

## クイックスタート

流れ：**インストール** → **Python SDK** → **CLI** → **HTTP API と Dashboard** → **MCP**。設定は [.env.example](.env.example) と [設定ガイド](docs/guides/0003-configuration.md)。

### インストール

```bash
pip install powermem
```

### 基本的な使い方（SDK）

```python
from powermem import Memory, auto_config

config = auto_config()
memory = Memory(config=config)

memory.add("ユーザーはコーヒーが好き", user_id="user123")

results = memory.search("ユーザー設定", user_id="user123")
for result in results.get('results', []):
    print(f"- {result.get('memory')}")
```

詳細：[はじめに](docs/guides/0001-getting_started.md)。

### PowerMem CLI（1.0.0+）

`pmem` でメモリ操作、設定、バックアップ/復元、対話シェル。

```bash
pmem memory add "ユーザーはダークモードを好む" --user-id user123
pmem memory search "設定" --user-id user123

pmem config show
pmem config init
pmem stats --json

pmem shell
```

参照：[CLI 使用ガイド](docs/guides/0012-cli_usage.md)。

### HTTP API Server と Dashboard

SDK と **同じ** `.env` と PowerMem コア。REST、`/dashboard/`、`/docs`（OpenAPI）を提供。

```bash
powermem-server --host 0.0.0.0 --port 8000
```

Docker / Compose：

```bash
docker run -d \
  --name powermem-server \
  -p 8000:8000 \
  -v $(pwd)/.env:/app/.env:ro \
  --env-file .env \
  oceanbase/powermem-server:latest

docker-compose -f docker/docker-compose.yml up -d
```

詳細：[API Server](docs/api/0005-api_server.md)。

### MCP Server

同じ SDK / `.env`。Claude Desktop 等の MCP クライアント向け。

```bash
pip install powermem
# uv / uvx: https://docs.astral.sh/uv/getting-started/

uvx powermem-mcp sse
uvx powermem-mcp sse 8001
uvx powermem-mcp stdio
uvx powermem-mcp streamable-http
uvx powermem-mcp streamable-http 8001
```

Claude Desktop（SSE）設定例：

```json
{
  "mcpServers": {
    "powermem": {
      "url": "http://localhost:8000/mcp"
    }
  }
}
```

詳細：[MCP Server](docs/api/0004-mcp.md)。

## エコシステムと統合

### サンプル

- **LangChain**：[医療サポート例](examples/langchain/README.md)
- **LangGraph**：[カスタマーサービス例](examples/langgraph/README.md)

## ドキュメント

| トピック | リンク |
|---------|--------|
| はじめに | [Guide](docs/guides/0001-getting_started.md) |
| CLI | [CLI](docs/guides/0012-cli_usage.md) |
| 設定 | [Configuration](docs/guides/0003-configuration.md) |
| マルチエージェント | [Multi-Agent](docs/guides/0005-multi_agent.md) |
| 統合 | [Integrations](docs/guides/0009-integrations.md) |
| サブストア | [Sub stores](docs/guides/0006-sub_stores.md) |
| API | [API overview](docs/api/overview.md) |
| アーキテクチャ | [Architecture](docs/architecture/overview.md) |
| 例 | [Examples](docs/examples/overview.md) |
| 開発 | [Development](docs/development/overview.md) |

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
