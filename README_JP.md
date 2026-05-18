# PowerMem

**AI アプリケーションとエージェント向けの、永続化・自己進化型のメモリ層。**

[![PyPI version](https://img.shields.io/pypi/v/powermem)](https://pypi.org/project/powermem/)
[![PyPI downloads](https://img.shields.io/pypi/dm/powermem)](https://pypi.org/project/powermem/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://pypi.org/project/powermem/)
[![License Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![GitHub](https://img.shields.io/badge/GitHub-oceanbase%2Fpowermem-181717?logo=github)](https://github.com/oceanbase/powermem)
[![Discord](https://img.shields.io/badge/Discord-community-5865F2?logo=discord&logoColor=white)](https://discord.com/invite/74cF8vbNEs)

*[English](README.md) · [中文](README_CN.md) · 日本語*

PowerMem はベクトル・全文・グラフ検索に LLM 駆動のメモリ抽出とエビングハウス型の時間減衰を組み合わせます。**経験 (Experience) + スキル (Skill) の二層蒸留**による自己進化型メモリ、マルチエージェント分離、ユーザープロフィール、そしてテキスト/画像/音声のマルチモーダル信号を標準搭載しています。

---

## ベンチマーク

### [LOCOMO](https://github.com/snap-research/locomo)

| 指標 | PowerMem | ベースライン | 改善 |
|------|----------|----------------------------------|------|
| 精度 | **87.79%** | 52.9% | **+65.9%** |
| 検索 p95 遅延 | **1.44 s** | 17.12 s | **-91.6%** |
| トークン | **~0.9 k** | 26 k | **-96.5%** |

### [AppWorld](https://github.com/StonyBrookNLP/appworld)

| 指標 | PowerMem | ベースライン | 改善 |
|------|----------|------------------------------|------|
| 通過率 | **39%** | 24% | **+62.5%** |
| 平均ステップ | **6.2** | 9.5 | **-34.7%** |
| 総トークン | **1.74 M** | 2.56 M | **-32.0%** |

再現スクリプト: [`benchmark/`](benchmark/)。背後の仕組み: **経験 + スキル 二層蒸留 + 4 経路ハイブリッド検索 + LLM 自動マージ**（API: `memory.distill_all() / add_skill / add_experience / search_*`、サンプル [`examples/experience_skill_usage.py`](examples/experience_skill_usage.py)）。

---

## 連携 — クライアントを選んで一行で接続

PowerMem は主要な AI クライアント向けに公式プラグインを提供します。すべて同じバックエンド（HTTP サーバーまたはローカルの `pmem` CLI）を共有するため、クライアントごとに設定スキーマを書き直す必要はありません。

| クライアント / フレームワーク | 一行インストール | モード |
|------------------------------|-----------------|--------|
| OpenClaw（ClawdBot） | `openclaw plugins install memory-powermem` | CLI（デフォルト）/ HTTP（任意） |
| Claude Code | `git clone https://github.com/oceanbase/powermem && claude --plugin-dir powermem/apps/claude-code-plugin` | HTTP（デフォルト）/ MCP（任意） |
| Cursor / VS Code / Codex / Windsurf / GitHub Copilot | [PowerMem VS Code 拡張](apps/vscode-extension/) をインストールし **PowerMem: Link to AI tools** を実行 | MCP または HTTP（クライアント毎） |
| Claude Desktop / Cline / 任意の MCP クライアント | `uvx powermem-mcp sse` | MCP（SSE / stdio / streamable-http） |
| LangChain / LangGraph | `pip install powermem`、下記 [サンプル](#サンプル) を参照 | Python SDK |
| Go / Java / TypeScript アプリ | 下記 [SDK 一覧](#sdk-一覧) を参照 | HTTP REST |

### OpenClaw（ClawdBot）

[OpenClaw](https://github.com/openclaw/openclaw) はプラグイン [`memory-powermem`](https://github.com/ob-labs/memory-powermem) によって長期メモリを獲得します。

```bash
openclaw plugins install memory-powermem
```

デフォルトは **CLI モード** — プラグイン内部で同梱の `pmem` を呼び出し、`~/.openclaw/` 配下の SQLite にデータを書き込み、OpenClaw が既に注入しているモデルを再利用します。別途サーバーの起動も、専用の `.env` も必要ありません。チーム共有の PowerMem API を使いたい場合は **HTTP モード** に切り替えてください（プラグインの `config` で `mode: "http"` と `requestConfig.memory_db` を設定。詳細はプラグインの README を参照）。

<div align="center">

<img src="docs/images/openclaw_powermem.jpeg" alt="PowerMem と OpenClaw" width="640"/>

</div>

### Claude Code

```bash
# 本リポジトリから直接ロード（開発/デバッグ向け）
claude --plugin-dir /path/to/powermem/apps/claude-code-plugin

# あるいは zip にパッケージして配布し、解凍後のディレクトリを --plugin-dir に指定
make package-claude-plugin   # 生成物: apps/claude-code-plugin/dist/<version>.zip
```

デフォルトは **HTTP モード**、即利用可能:

- `UserPromptSubmit` → `POST /api/v1/memories/search`、上位結果が `additionalContext` として現在の会話に注入されます。
- `SessionEnd` / `PostCompact` → `POST /api/v1/memories`、会話全体または圧縮サマリをメモリへ書き戻します。
- 端末側に **Python は不要** — フックは事前ビルド済みのネイティブバイナリ（macOS / Linux / Windows）として配布されます。

Claude が会話中に `search_memories` / `add_memory` ツールを明示的に呼び出す **MCP モード** に切り替えることもできます:

```bash
bash scripts/apply-connection-mode.sh mcp
```

詳細は [`apps/claude-code-plugin/README.md`](apps/claude-code-plugin/README.md) を参照してください。

### Cursor / VS Code / Codex / Windsurf / GitHub Copilot

**PowerMem VS Code 拡張**を一度インストールするだけ（VS Code と Cursor の両方で動作）。**PowerMem: Link to AI tools** コマンドを実行すると、サポート対象の各クライアントに対して MCP または HTTP 設定が自動で書き込まれます:

| クライアント | 書き込まれる設定パス |
|--------------|----------------------|
| Cursor | `~/.cursor/mcp.json`（マージ書き込み） |
| Claude（Desktop / Code） | `~/.claude/providers/powermem.json` |
| Codex | `~/.codex/context.json`（マージ書き込み） |
| Windsurf | `~/.windsurf/context/powermem.json` |
| GitHub Copilot | `~/.github/copilot/powermem.json` |

同じ拡張は **Query memories**、**Add selection to memory**、**Quick note**、およびステータスバーの **Dashboard** も提供します。詳細は [`apps/vscode-extension/README.md`](apps/vscode-extension/README.md)。

### 任意の MCP クライアント（Claude Desktop、Cline ……）

```bash
uvx powermem-mcp sse                  # SSE、デフォルト :8000（推奨）
uvx powermem-mcp stdio                # stdio
uvx powermem-mcp streamable-http      # streamable HTTP
```

Claude Desktop / 多くの MCP クライアント向けの設定:

```json
{
  "mcpServers": {
    "powermem": { "url": "http://localhost:8000/mcp" }
  }
}
```

公開されるツール: `add_memory`、`search_memories`、`get_memory_by_id`、`update_memory`、`delete_memory`、`delete_all_memories`、`list_memories`。詳細は [MCP Server](docs/api/0004-mcp.md) を参照。

### LangChain & LangGraph

```bash
pip install powermem langchain langchain-openai
```

エンドツーエンドで実行できるサンプル:

- [LangChain 医療アシスタント Bot](examples/langchain/README.md)
- [LangGraph カスタマーサポート Bot](examples/langgraph/README.md)

### SDK 一覧

| 言語 | パッケージ / リポジトリ |
|------|------------------------|
| Python | `pip install powermem`（本リポジトリ） |
| Go | [`ob-labs/powermem-go`](https://github.com/ob-labs/powermem-go) |
| Java | [`ob-labs/powermem-java`](https://github.com/ob-labs/powermem-java) |
| TypeScript | [`ob-labs/powermem-ts`](https://github.com/ob-labs/powermem-ts) |

---

## クイックスタート（Python SDK）

**前提:** [.env.example](.env.example) を `.env` にコピーし、**LLM** と **埋め込み（embedding）** を設定してください。デフォルト DB は SQLite。OceanBase バックエンドでは **埋め込み SeekDB** を使えるため、別途データベースを立ち上げる必要はありません。インストール後は `pmem config init` で対話的に同じ設定を生成できます。詳しくは [はじめに](docs/guides/0001-getting_started.md) を参照してください。

### インストール

```bash
pip install powermem
```

### SDK サンプル

設定済みの `.env` があるディレクトリで実行します:

```python
from powermem import Memory, auto_config

memory = Memory(config=auto_config())

memory.add("ユーザーはコーヒーが好き", user_id="user123")

for r in memory.search("ユーザー設定", user_id="user123").get("results", []):
    print("-", r.get("memory"))
```

詳しくは [はじめに](docs/guides/0001-getting_started.md) を参照。

### CLI（`pmem`、1.0+）

```bash
pmem memory add "ユーザーはダークモードを好む" --user-id user123
pmem memory search "設定" --user-id user123
pmem shell                           # 対話 REPL
```

詳細は [CLI 使用ガイド](docs/guides/0012-cli_usage.md)。

### HTTP API Server と Dashboard

SDK と同じ `.env` を使用。Dashboard は `/dashboard/` 以下に提供されます。

```bash
powermem-server --host 0.0.0.0 --port 8000
```

Docker / Compose は [API Server](docs/api/0005-api_server.md) と [Docker README](docker/README.md) を参照。公式イメージ: `oceanbase/powermem-server:latest`。

---

## 機能概要

**メモリパイプラインと検索** — [スマート抽出と更新](docs/examples/scenario_2_intelligent_memory.md)；[経験 + スキル 二層蒸留（自己進化）](docs/examples/scenario_6_sub_stores.md)；[エビングハウス型減衰](docs/examples/scenario_8_ebbinghaus_forgetting_curve.md)；[ハイブリッド検索（ベクトル / 全文 / グラフ）](docs/examples/scenario_2_intelligent_memory.md)；[サブストアとルーティング](docs/examples/scenario_6_sub_stores.md)。

**プロフィールとマルチエージェント** — [ユーザープロフィール](docs/examples/scenario_9_user_memory.md)；[共有 / 分離メモリとスコープ](docs/examples/scenario_3_multi_agent.md)。

**マルチモーダル** — [テキスト / 画像 / 音声](docs/examples/scenario_7_multimodal.md)。

**Provider 一覧**

| レイヤー | 標準搭載の Provider |
|----------|---------------------|
| LLM | Anthropic、OpenAI、Azure OpenAI、Gemini、Qwen（+ ASR）、DeepSeek、Ollama、vLLM、SiliconFlow、Z.AI、LangChain ラッパー |
| Embedding | OpenAI、Azure OpenAI、Qwen（+ VL マルチモーダル、スパース）、Gemini、Vertex AI、AWS Bedrock、Ollama、LM Studio、HuggingFace、Together、SiliconFlow、Z.AI、OceanBase MASS、LangChain ラッパー |
| Rerank | Jina、Qwen、Z.AI、汎用 |
| Storage | OceanBase（+ グラフ）、埋め込み SeekDB、PostgreSQL/pgvector、SQLite |

---

## ドキュメント

- [はじめに](docs/guides/0001-getting_started.md) — インストール、`.env`、最初の `Memory` 利用
- [設定](docs/guides/0003-configuration.md) — 設定モデル、ストレージバックエンド、環境変数
- [アーキテクチャ](docs/architecture/overview.md) — 主要コンポーネント、ストレージ構成、検索の流れ
- [API とサービス](docs/api/overview.md) — REST、MCP、HTTP サーバー、Python 向け API
- [CLI](docs/guides/0012-cli_usage.md) — `pmem` コマンド、対話シェル、バックアップとマイグレーション
- [マルチエージェント](docs/guides/0005-multi_agent.md) — スコープ、分離、エージェント間共有
- [連携](docs/guides/0009-integrations.md) — LangChain などフレームワーク連携
- [Docker とデプロイ](docker/README.md) — イメージ、Compose、API サーバーの実行
- [開発](docs/development/overview.md) — ローカル環境、テスト、コントリビューション

その他: [サブストア](docs/guides/0006-sub_stores.md)、[ガイド一覧](docs/guides/overview.md)。

## サンプル

- [シナリオと Notebook](docs/examples/overview.md) — ユースケース別の手順（基本利用、マルチモーダル、忘却曲線、スパースベクトル、サブストアなど）
- 上記 [連携](#連携--クライアントを選んで一行で接続) セクションも参照（OpenClaw、Claude Code、VS Code 拡張、MCP、LangChain、LangGraph）。

## リリースハイライト

| バージョン | 日付 | 内容 |
|------------|------|------|
| 1.2.0 | 2026-04 | 経験 + スキル 二層蒸留と `distill_all()`（自己進化型メモリ、AppWorld +15 pts）；OB MASS Embedding；Qwen VL マルチモーダル Embedding；OceanBase Zero Mode 互換；LOCOMO 精度を 87.79% に引き上げ |
| 1.1.0 | 2026-04-02 | OceanBase 向けに埋め込み SeekDB（別途 DB サービス不要）；[IDE 連携](apps/README.md)（VS Code 拡張、Claude Code プラグイン） |
| 1.0.0 | 2026-03-16 | CLI（`pmem`）：メモリ操作、設定、バックアップ/復元/マイグレーション、対話シェル、補完；Web Dashboard |
| 0.5.0 | 2026-02-06 | SDK/API 設定の統一（pydantic-settings）；OceanBase native hybrid search；メモリクエリと一覧ソート；プロフィールの言語カスタマイズ |
| 0.4.0 | 2026-01-20 | スパースベクトル混合検索；プロフィール起点のクエリ書き換え；スキーマ更新と移行ツール |
| 0.3.0 | 2026-01-09 | 本番向け HTTP API Server；Docker |
| 0.2.0 | 2025-12-16 | プロフィール強化；マルチモーダル（テキスト/画像/音声） |
| 0.1.0 | 2025-11-14 | コアメモリとハイブリッド検索；LLM 抽出；忘却曲線；マルチエージェント；OceanBase/PostgreSQL/SQLite；グラフ検索 |

## サポート

- [GitHub Issues](https://github.com/oceanbase/powermem/issues)
- [GitHub Discussions](https://github.com/oceanbase/powermem/discussions)

## ライセンス

Apache License 2.0 — 詳細は [LICENSE](LICENSE)。
