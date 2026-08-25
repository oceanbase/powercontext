# PowerContext

**記憶を超えて**

[![PyPI version](https://img.shields.io/pypi/v/powercontext)](https://pypi.org/project/powercontext/)
[![License Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Discord](https://img.shields.io/badge/Discord-community-5865F2?logo=discord&logoColor=white)](https://discord.com/invite/74cF8vbNEs)

*[English](README.md) · [中文](README_CN.md) · [日本語](README_JP.md)*

PowerContext は [PowerMem](https://www.powermem.ai/) のアップグレード版であり、人と Agent の協働を支える
コンテキストランタイムです。共同で進めた作業を、理解・引き継ぎ・継続が可能なプロジェクトコンテキストとして
蓄積します。

## クイックスタート

macOS または Linux、Python 3.11 以降、[`uv`](https://docs.astral.sh/uv/)、および少なくとも 1 つの対応 Agent Host が必要です。

### 1. PowerContext とインテグレーションをインストールする

```bash
uv tool install "powercontext[cli,server]==0.0.2"

# 1 つ以上のインテグレーションを選択します。
powercontext setup codex --source oceanbase/powercontext --ref v0.0.2
powercontext setup claude-code --source oceanbase/powercontext --ref v0.0.2
powercontext setup dsh --source oceanbase/powercontext --ref v0.0.2
powercontext setup hermes --source oceanbase/powercontext --ref v0.0.2

# OpenClaw は現在、master から対応する CLI とインテグレーションをインストールする必要があります。
uv tool install --force "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@master"
powercontext setup openclaw --source oceanbase/powercontext --ref master
```

最初のコマンドは、隔離された環境に最新リリースの CLI とローカル Server をインストールします。リリース版の setup
コマンドは、対応するリポジトリの tag から各インテグレーションをインストールします。OpenClaw がリリースに含まれる
までは、追加の `uv tool install` コマンドによって CLI、Server、インテグレーションを同じ `master` revision に
そろえます。既存のインストールを更新するには、setup をもう一度実行してください。

### 2. ローカル Server を起動して検証する

1 つのターミナルで Server を起動したままにします。

```bash
powercontext server run
```

別のターミナルでサービスとプラグインを検証します。

```bash
powercontext doctor
powercontext doctor codex  # または: claude-code / dsh / hermes / openclaw
```

デフォルトでは、Server は `127.0.0.1:8000` で待ち受け、`/mcp` で Streamable HTTP MCP を公開し、
ローカルの SQLite データベースにデータを永続化します。明示的な Memory 操作には inference provider の設定は
不要です。

## 主な機能

| 機能 | 提供する価値 |
| --- | --- |
| Memory の抽出と管理 | 長期的に再利用する価値のある意思決定、制約、成果、状態、次のステップを明示的に記録します。Generation model を設定すると、Source から Memory を抽出することもできます。改訂や廃止を行っても履歴は保持されます |
| リクエスト時の範囲限定想起 | Agent がリクエストを処理する前に、プロジェクト scope、関連性、byte budget に基づいて、schema 検証済みで citation 付きの `PreparedContext` を 1 つ生成します。想起に失敗しても元のタスクは中断されません |
| Handoff によるタスク引き継ぎ | 目標、検証済みの進捗、ブロッカー、次のステップ、エビデンスを検査可能な作業パッケージにまとめ、別のセッション、タスク、モデル、Agent Host が明確な状態から作業を継続できるようにします |
| Source とエビデンスチェーン | 知識の出所を保存し、正確な citation で Memory と Artifact を関連付けます。Prompt の収集によって作成されるのは Source だけであり、直接 Memory に変換されることはありません |
| Experience と Skill のガバナンス | モデルや呼び出し元が作成できるのは Candidate までです。Review を通過して初めて不変の revision が作成され、Skill にはさらに明示的な export が必要です。自動的に承認、インストール、実行されることはありません |
| ローカルおよびサービスデプロイ | ローカル開発では SQLite をそのまま使用でき、チーム環境では OceanBase を選択できます。HTTP/OpenAPI、MCP、認証、OpenTelemetry を通じて既存システムと連携できます |

## ベンチマーク

### [LoCoMo](https://github.com/snap-research/locomo)

![LOCOMO benchmark comparison showing PowerContext accuracy, search latency, and answer token usage against PowerMem and a full-context baseline](docs/assets/locomo-benchmark-comparison.svg)

### [SWE-bench Pro public v2](https://github.com/scaleapi/SWE-bench_Pro-os)

![SWE-bench Pro public v2 comparison showing an increase from 82.35% with PowerContext off to 86.73% with PowerContext on](docs/assets/swe-bench-pro-public-v2-comparison.svg)

この評価は Codex 環境で実行し、PowerContext OFF と ON の両グループで `gpt-5.6-sol` モデルを使用しました。

---

## インテグレーション

PowerContext は Codex、Claude Code、DeepSeek Harness、Hermes Agent、Pi Coding Agent、OpenClaw、WorkBuddy 向けの公式インテグレーションと
インストールガイドを提供します。これらのインテグレーションは、PowerContext Server を通じて同じスコープ付きデータと
履歴を保持する契約を使用します。ホストインテグレーションが Server を自動的に起動したり、組み込んだりすることはありません。

### 公式インテグレーション

<table>
<tr>
<td align="center" width="120"><a href="docs/en/docs/how-to/configure-codex.md"><img src="https://github.com/openai.png?size=120" alt="Codex" width="48" height="48" /><br /><sub><b>Codex</b></sub></a></td>
<td align="center" width="120"><a href="docs/en/docs/how-to/configure-claude-code.md"><img src="https://github.com/anthropics.png?size=120" alt="Claude Code" width="48" height="48" /><br /><sub><b>Claude Code</b></sub></a></td>
<td align="center" width="120"><a href="docs/en/docs/how-to/configure-dsh.md"><img src="https://github.com/deepseek-ai.png?size=120" alt="DeepSeek Harness" width="48" height="48" /><br /><sub><b>DeepSeek Harness</b></sub></a></td>
<td align="center" width="120"><a href="integrations/hermes/README.md"><img src="https://github.com/NousResearch/hermes-agent/blob/main/website/static/img/logo.png?raw=true&size=120" alt="Hermes Agent" width="48" height="48" /><br /><sub><b>Hermes Agent</b></sub></a></td>
<td align="center" width="120"><a href="docs/en/docs/how-to/configure-pi.md"><img src="https://github.com/earendil-works.png?size=120" alt="Pi Coding Agent" width="48" height="48" /><br /><sub><b>Pi Coding Agent</b></sub></a></td>
<td align="center" width="120"><a href="docs/en/docs/how-to/configure-openclaw.md"><img src="https://github.com/openclaw.png?size=120" alt="OpenClaw" width="48" height="48" /><br /><sub><b>OpenClaw</b></sub></a></td>
<td align="center" width="120"><a href="integrations/workbuddy/README.md"><img src="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 48 48'%3E%3Crect width='48' height='48' rx='10' fill='%231f6feb'/%3E%3Ctext x='24' y='31' font-family='Arial,Helvetica,sans-serif' font-size='18' font-weight='bold' fill='%23ffffff' text-anchor='middle'%3EWB%3C/text%3E%3C/svg%3E" alt="WorkBuddy" width="48" height="48" /><br /><sub><b>WorkBuddy</b></sub></a></td>
</tr>
</table>

## 開発

ロックされた開発環境と Hook をインストールします。

```bash
make install
```

Pull Request を作成する前に、主要な検証コマンドを実行します。

```bash
make check
make test
make docs-test
```

`openapi/powercontext.yaml` を変更した後は、`make contract-test` を実行してください。完全なワークフローについては
[CONTRIBUTING.md](CONTRIBUTING.md)、実装ガイドについては
[`docs/en/development/`](docs/en/development/core-protocol.md)を参照してください。

## コミュニティ

質問やフィードバックは [Discord](https://discord.com/invite/74cF8vbNEs) で歓迎しています。再現可能な不具合や、
範囲を明確にした機能リクエストには [GitHub Issues](https://github.com/oceanbase/powercontext/issues) を利用してください。

## ライセンス

PowerContext は [Apache License 2.0](LICENSE) の下でライセンスされています。
