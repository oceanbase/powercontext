# PowerMem

**AI アプリケーションとエージェント向けの永続メモリ層。**

[![PyPI version](https://img.shields.io/pypi/v/powermem)](https://pypi.org/project/powermem/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://pypi.org/project/powermem/)
[![License Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![GitHub](https://img.shields.io/badge/GitHub-oceanbase%2Fpowermem-181717?logo=github)](https://github.com/oceanbase/powermem)
[![Discord](https://img.shields.io/badge/Discord-community-5865F2?logo=discord&logoColor=white)](https://discord.com/invite/74cF8vbNEs)

*[English](README.md) · [中文](README_CN.md) · 日本語*

PowerMem はベクトル・全文・グラフ検索に加え、LLM によるメモリ抽出とエビングハウス型の時間減衰、マルチエージェント分離、ユーザープロフィール、テキスト・画像・音声などのマルチモーダル手がかりを扱います。

Python SDK、CLI（`pmem`）、HTTP API Server（Dashboard 付き）、MCP Server は同一の `.env` を共有します。[.env.example](.env.example) と [設定ガイド](docs/guides/0003-configuration.md) を参照してください。

> **ニュース:** [OpenClaw](https://github.com/openclaw/openclaw) はプラグイン [memory-powermem](https://github.com/ob-labs/memory-powermem) により PowerMem を長期メモリとして利用できます（`openclaw plugins install memory-powermem`）。

## クイックスタート

### インストール

```bash
pip install powermem
```

### SDK サンプル

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

### その他の利用形態

| 形態 | 代表的なコマンド | ドキュメント |
|------|------------------|--------------|
| CLI | `pmem memory add` / `pmem memory search`；`pmem shell` | [CLI 使用ガイド](docs/guides/0012-cli_usage.md) |
| HTTP + Dashboard | `powermem-server --host 0.0.0.0 --port 8000`；イメージ `oceanbase/powermem-server:latest`；`docker-compose -f docker/docker-compose.yml` | [API Server](docs/api/0005-api_server.md) |
| MCP | `uvx powermem-mcp sse`（stdio / streamable-http も可）；`powermem` と `uv` が必要 | [MCP Server](docs/api/0004-mcp.md) |

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

## ドキュメント

- [はじめに](docs/guides/0001-getting_started.md) — インストール、`.env`、最初の `Memory` 利用
- [設定](docs/guides/0003-configuration.md) — 設定モデル、ストレージバックエンド、環境変数
- [アーキテクチャ](docs/architecture/overview.md) — 主要コンポーネント、ストレージ構成、検索の流れ
- [API とサービス](docs/api/overview.md) — REST、MCP、HTTP サーバー、Python 向け API
- [CLI](docs/guides/0012-cli_usage.md) — `pmem` コマンド、対話シェル、バックアップとマイグレーション
- [マルチエージェント](docs/guides/0005-multi_agent.md) — スコープ、分離、エージェント間共有
- [統合](docs/guides/0009-integrations.md) — LangChain などフレームワーク連携
- [Docker とデプロイ](docker/README.md) — イメージ、Compose、API サーバーの実行
- [開発](docs/development/overview.md) — ローカル環境、テスト、コントリビューション

その他：[サブストア](docs/guides/0006-sub_stores.md)、[ガイド一覧](docs/guides/overview.md)。

## サンプル

- [シナリオと Notebook](docs/examples/overview.md) — ユースケース別の手順とノート（基本、マルチモーダル、忘却曲線など）
- [LangChain サンプル](examples/langchain/README.md) — 医療サポートチャットボット（LangChain + PowerMem + OceanBase）
- [LangGraph サンプル](examples/langgraph/README.md) — カスタマーサービスボット（LangGraph + PowerMem + OceanBase）

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

- [GitHub Issues](https://github.com/oceanbase/powermem/issues)
- [GitHub Discussions](https://github.com/oceanbase/powermem/discussions)

## ライセンス

Apache License 2.0 — 詳細は [LICENSE](LICENSE)。
