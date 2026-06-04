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
|------|----------|-------------------------|-------------|
| 精度 | **87.79%** | 52.9% | **+65.9%** |
| 検索 p95 遅延 | **1.44 s** | 17.12 s | **-91.6%** |
| トークン | **~0.9 k** | 26 k | **-96.5%** |

### [AppWorld](https://github.com/StonyBrookNLP/appworld)

| 指標 | PowerMem | ベースライン | 改善 |
|------|----------|-------------------------|-------------|
| 通過率 | **39%** | 24% | **+62.5%** |
| 平均ステップ | **6.2** | 9.5 | **-34.7%** |
| 総トークン | **1.74 M** | 2.56 M | **-32.0%** |

再現スクリプト: [`benchmark/`](benchmark/)。背後の仕組み: **経験 + スキル 二層蒸留 + 4 経路ハイブリッド検索 + LLM 自動マージ**（API: `memory.distill_all() / add_skill / add_experience / search_*`、サンプル [`examples/experience_skill_usage.py`](examples/experience_skill_usage.py)）。

---

## 連携 — クライアントを選んで一行で接続

PowerMem は主要な AI クライアント向けに公式プラグインとセットアップガイドを提供します。いずれも同じバックエンド（HTTP サーバー、MCP サーバー、またはローカルの `pmem` CLI）を指すため、クライアントごとに設定スキーマを書き直す必要はありません。すべてのエージェントが同じメモリサーバーを共有します。

### AI エージェントと IDE

<table>
<tr>
<td align="center" width="120"><a href="#claude-code"><img src="https://github.com/anthropics.png?size=120" alt="Claude Code" width="48" height="48" /></a><br /><a href="#claude-code"><sub><b>Claude Code</b></sub></a></td>
<td align="center" width="120"><a href="#cursor-vs-code-windsurf-github-copilot-qoder"><picture><source media="(prefers-color-scheme: dark)" srcset="https://svgl.app/library/cursor_dark.svg"><img src="https://svgl.app/library/cursor_light.svg" alt="Cursor" width="48" height="48" /></picture></a><br /><a href="#cursor-vs-code-windsurf-github-copilot-qoder"><sub><b>Cursor</b></sub></a></td>
<td align="center" width="120"><a href="#cursor-vs-code-windsurf-github-copilot-qoder"><img src="https://svgl.app/library/vscode.svg" alt="VS Code" width="48" height="48" /></a><br /><a href="#cursor-vs-code-windsurf-github-copilot-qoder"><sub><b>VS Code</b></sub></a></td>
<td align="center" width="120"><a href="#any-mcp-client"><img src="https://github.com/openai.png?size=120" alt="Codex" width="48" height="48" /></a><br /><a href="#any-mcp-client"><sub><b>Codex</b></sub></a></td>
<td align="center" width="120"><a href="#cursor-vs-code-windsurf-github-copilot-qoder"><picture><source media="(prefers-color-scheme: dark)" srcset="https://svgl.app/library/windsurf-dark.svg"><img src="https://svgl.app/library/windsurf-light.svg" alt="Windsurf" width="48" height="48" /></picture></a><br /><a href="#cursor-vs-code-windsurf-github-copilot-qoder"><sub><b>Windsurf</b></sub></a></td>
<td align="center" width="120"><a href="#cursor-vs-code-windsurf-github-copilot-qoder"><img src="https://github.githubassets.com/images/modules/site/copilot/copilot.png" alt="GitHub Copilot" width="48" height="48" /></a><br /><a href="#cursor-vs-code-windsurf-github-copilot-qoder"><sub><b>GitHub Copilot</b></sub></a></td>
</tr>
<tr>
<td align="center" width="120"><a href="#cursor-vs-code-windsurf-github-copilot-qoder"><img src="https://github.com/QoderAI.png?size=120" alt="Qoder" width="48" height="48" /></a><br /><a href="#cursor-vs-code-windsurf-github-copilot-qoder"><sub><b>Qoder</b></sub></a></td>
<td align="center" width="120"><a href="#any-mcp-client"><picture><source media="(prefers-color-scheme: dark)" srcset="https://svgl.app/library/opencode-dark.svg"><img src="https://svgl.app/library/opencode.svg" alt="OpenCode" width="48" height="48" /></picture></a><br /><a href="#any-mcp-client"><sub><b>OpenCode</b></sub></a></td>
<td align="center" width="120"><a href="#openclaw-clawdbot"><img src="https://github.com/openclaw.png?size=120" alt="OpenClaw" width="48" height="48" /></a><br /><a href="#openclaw-clawdbot"><sub><b>OpenClaw</b></sub></a></td>
<td align="center" width="120"><a href="#any-mcp-client"><img src="https://github.com/anthropics.png?size=120" alt="Claude Desktop" width="48" height="48" /></a><br /><a href="#any-mcp-client"><sub><b>Claude Desktop</b></sub></a></td>
<td align="center" width="120"><a href="#any-mcp-client"><img src="https://github.com/cline.png?size=120" alt="Cline" width="48" height="48" /></a><br /><a href="#any-mcp-client"><sub><b>Cline</b></sub></a></td>
<td></td>
</tr>
</table>

### SDK とアプリ

| アプリ / フレームワーク | 詳細 |
|-----------------|---------|
| Python SDK | `pip install powermem`、[クイックスタート](#quick-start-python-sdk) を参照 |
| LangChain / LangGraph | `pip install powermem`、[LangChain ガイド](docs/integrations/langchain.md) を参照 |
| Go アプリ | [SDK](#sdks) |
| Java アプリ | [SDK](#sdks) |
| TypeScript アプリ | [SDK](#sdks) |
| 任意の MCP クライアント | `powermem-mcp sse`（デフォルト :8848）、[MCP クライアントガイド](docs/integrations/mcp_client.md) |
| HTTP REST アプリ | `powermem-server --host 0.0.0.0 --port 8848`、[API サーバー](docs/api/0005-api_server.md) |

<a id="openclaw-clawdbot"></a>

### OpenClaw（ClawdBot）

[OpenClaw](https://github.com/openclaw/openclaw) はプラグイン [`memory-powermem`](https://github.com/ob-labs/memory-powermem) によって長期メモリを獲得します。

```bash
openclaw plugins install memory-powermem
```

デフォルトは **CLI モード** — プラグインが同梱の `pmem` を呼び出し、`~/.openclaw/` 配下の SQLite に書き込み、OpenClaw が既に注入しているモデルを再利用します。別サーバーも追加の `.env` も不要です。チーム共有の PowerMem API を使う場合は **HTTP モード** に切り替えます（プラグイン README の `requestConfig.memory_db` を参照）。

詳細ガイド: [OpenClaw 連携](docs/integrations/openclaw.md)。

<div align="center">

<img src="docs/images/openclaw_powermem.jpeg" alt="PowerMem と OpenClaw" width="640"/>

</div>

<a id="claude-code"></a>

### Claude Code

#### 最速の手順 — Claude Code にセットアップさせる

まずコードを取得してディレクトリに入ります:

```bash
git clone https://github.com/oceanbase/powermem
cd powermem
```

ターミナルで Claude Code を開き、次の一行を貼り付けます:

```text
Read and follow apps/claude-code-plugin/SETUP.md to set up PowerMem memory for Claude Code.
```

Claude Code は [`apps/claude-code-plugin/SETUP.md`](apps/claude-code-plugin/SETUP.md) を読み、必要な秘密情報だけを尋ね、エンドツーエンドで設定を完了します。

#### 手動セットアップ

手で配線したい場合は、環境変数、MCP モード、`remember` / `recall` スキル、Windows フック、トラブルシューティング、アンインストールまでの手順を **[docs/integrations/claude_code.md](docs/integrations/claude_code.md)** にまとめています。

<a id="cursor-vs-code-windsurf-github-copilot-qoder"></a>

### Cursor、VS Code、Windsurf、GitHub Copilot、Qoder

#### 推奨 — IDE エージェントにセットアップさせる

まずコードを取得してディレクトリに入ります:

```bash
git clone https://github.com/oceanbase/powermem
cd powermem
```

IDE の AI エージェントウィンドウに次の一行を貼り付けます:

```text
Read and follow apps/vscode-extension/SETUP.md to setup PowerMem
```

エージェントは [`apps/vscode-extension/SETUP.md`](apps/vscode-extension/SETUP.md) に従います。再利用可能な `powermem-server` HTTP API バックエンドを優先し、HTTP が使えない場合のみ MCP にフォールバックし、無関係なツールではなく現在の IDE/クライアントだけを設定します。

#### 手動セットアップ

手で配線する場合は IDE 別ガイドを参照:

| クライアント | 詳細 |
|--------|---------|
| VS Code | [`docs/integrations/vs_code.md`](docs/integrations/vs_code.md) |
| Cursor | [`docs/integrations/cursor.md`](docs/integrations/cursor.md) |
| Windsurf | [`docs/integrations/windsurf.md`](docs/integrations/windsurf.md) |
| GitHub Copilot | [`docs/integrations/github_copilot.md`](docs/integrations/github_copilot.md) |
| Qoder | [`docs/integrations/qoder.md`](docs/integrations/qoder.md) |

同じ拡張は **Query memories**、**Add selection to memory**、**Quick note**、およびステータスバーの **Dashboard** も提供します。詳細は [`apps/vscode-extension/README.md`](apps/vscode-extension/README.md) と [VS Code ガイド](docs/integrations/vs_code.md)。

<a id="any-mcp-client"></a>

### 任意の MCP クライアント

Claude Desktop、Codex、Cline、OpenCode、Roo Code、Goose、その他 MCP 対応クライアント向けです。MCP Client モードを使用してください。

まずコードを取得してディレクトリに入ります:

```bash
git clone https://github.com/oceanbase/powermem
cd powermem
```

MCP クライアントまたは IDE の AI エージェントウィンドウに次の一行を貼り付けます:

```text
Read and follow apps/mcp-client/SETUP.md to setup PowerMem
```

エージェントは [`apps/mcp-client/SETUP.md`](apps/mcp-client/SETUP.md) に従います。`powermem-mcp` を直接使い、ポート `8848` の SSE を優先し、必要な場合のみ streamable HTTP または stdio にフォールバックし、対象の MCP クライアントだけを設定します。

手で配線する場合は [汎用 MCP クライアントガイド](docs/integrations/mcp_client.md) を参照。後から削除する場合は [`apps/mcp-client/UNINSTALL.md`](apps/mcp-client/UNINSTALL.md)。公開ツール: `add_memory`、`search_memories`、`get_memory_by_id`、`update_memory`、`delete_memory`、`delete_all_memories`、`list_memories`。詳細: [MCP Server](docs/api/0004-mcp.md)。クライアント別メモ: [Cline](docs/integrations/cline.md)、[Codex](docs/integrations/codex.md)、[OpenCode](docs/integrations/opencode.md)。

### LangChain & LangGraph

```bash
pip install powermem langchain langchain-openai
```

エンドツーエンドで実行できるサンプル:

- [LangChain 医療アシスタント Bot](examples/langchain/README.md)
- [LangGraph カスタマーサポート Bot](examples/langgraph/README.md)

フレームワーク全体のガイド: [LangChain と LangGraph 連携](docs/integrations/langchain.md)。

<a id="sdks"></a>

### SDK

| 言語 | パッケージ / リポジトリ |
|----------|---------|
| Python | `pip install powermem`（本リポジトリ） |
| Go | [`ob-labs/powermem-go`](https://github.com/ob-labs/powermem-go) |
| Java | [`ob-labs/powermem-java`](https://github.com/ob-labs/powermem-java) |
| TypeScript | [`ob-labs/powermem-ts`](https://github.com/ob-labs/powermem-ts) |

---

<a id="quick-start-python-sdk"></a>

## クイックスタート（Python SDK）

**前提:** [.env.example](.env.example) を `.env` にコピーし、**LLM** の API キーだけを設定してください。ゼロコンフィグのローカルストレージを使う場合は `seekdb` extra（`pip install "powermem[seekdb]"`、または `server` / `mcp` との組み合わせ）をインストールしてください。これにより、デフォルトの **OceanBase** プロバイダは host 未設定時に **埋め込み seekdb** を起動できます。`seekdb` をインストールしない場合は、`OCEANBASE_HOST` でリモート OceanBase クラスタを指定するか、`sqlite` / `postgres` に切り替えてください。デフォルトの embedder はローカル実行の `all-MiniLM-L6-v2`（384 次元）で、API キー不要・初回利用時に自動ダウンロードされます。プロバイダ切り替えや高度な設定が必要な場合は [.env.example.full](.env.example.full) をコピーしてください。コンポーネントごとに全ての設定項目がまとめられています。インストール後は `pmem config init` で対話的に同じ設定を生成できます。詳しくは [はじめに](docs/guides/0001-getting_started.md) を参照してください。

### インストール

```bash
# コアのみ（SDK。CLI/server/MCP/seekdb は含まない）
pip install powermem

# CLI付き（pmem / powermem-cli）
pip install "powermem[cli]"

# HTTP API Server のみ（powermem-server。seekdb はインストールしない）
pip install "powermem[server]"

# MCP Server のみ（powermem-mcp。seekdb はインストールしない）
pip install "powermem[mcp]"

# seekdb 付き（ゼロコンフィグのローカルストレージ / embedder に必要）
pip install "powermem[seekdb]"

# HTTP API Server + seekdb
pip install "powermem[server,seekdb]"

# MCP Server + seekdb
pip install "powermem[mcp,seekdb]"

# 一般的なローカル全部入りインストール
pip install "powermem[cli,server,mcp,seekdb]"
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
powermem-server --host 0.0.0.0 --port 8848
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
| Storage | OceanBase（+ グラフ）、埋め込み seekdb、PostgreSQL/pgvector、SQLite |

---

## ドキュメント

- [はじめに](docs/guides/0001-getting_started.md) — インストール、`.env`、最初の `Memory` 利用
- [設定](docs/guides/0003-configuration.md) — 設定モデル、ストレージバックエンド、環境変数
- [アーキテクチャ](docs/architecture/overview.md) — 主要コンポーネント、ストレージ構成、検索の流れ
- [API とサービス](docs/api/overview.md) — REST、MCP、HTTP サーバー、Python 向け API
- [CLI](docs/guides/0012-cli_usage.md) — `pmem` コマンド、対話シェル、バックアップとマイグレーション
- [マルチエージェント](docs/guides/0005-multi_agent.md) — スコープ、分離、エージェント間共有
- [連携](docs/guides/0009-integrations.md) — LangChain などフレームワーク連携
- [エコシステム連携](docs/integrations/overview.md) — AI クライアントと IDE（[Claude Code](docs/integrations/claude_code.md) など）
- [Docker とデプロイ](docker/README.md) — イメージ、Compose、API サーバーの実行
- [開発](docs/development/overview.md) — ローカル環境、テスト、コントリビューション

その他: [サブストア](docs/guides/0006-sub_stores.md)、[ガイド一覧](docs/guides/overview.md)。

## サンプル

- [シナリオと Notebook](docs/examples/overview.md) — ユースケース別の手順（基本利用、マルチモーダル、忘却曲線、スパースベクトル、サブストアなど）
- クライアント / IDE 側の入口（OpenClaw、Claude Code、VS Code 拡張、MCP、LangChain、LangGraph）は上記 [連携](#連携--クライアントを選んで一行で接続) を参照。

## リリースハイライト

| バージョン | 日付 | 内容 |
|------------|------|------|
| 1.2.0 | 2026-04 | 経験 + スキル 二層蒸留と `distill_all()`（自己進化型メモリ、AppWorld +15 pts）；OB MASS Embedding；Qwen VL マルチモーダル Embedding；OceanBase Zero Mode 互換；LOCOMO 精度を 87.79% に引き上げ |
| 1.1.0 | 2026-04-02 | OceanBase 向けに埋め込み seekdb（別途 DB サービス不要）；[IDE 連携](apps/README.md)（VS Code 拡張、Claude Code プラグイン） |
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
