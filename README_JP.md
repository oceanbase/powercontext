# PowerContext

**記憶を超えて**

[![PyPI version](https://img.shields.io/pypi/v/powercontext)](https://pypi.org/project/powercontext/)
[![License Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Discord](https://img.shields.io/badge/Discord-community-5865F2?logo=discord&logoColor=white)](https://discord.com/invite/74cF8vbNEs)

*[English](README.md) · [中文](README_CN.md) · [日本語](README_JP.md)*

PowerContext は [PowerMem](https://www.powermem.ai/) のアップグレード版であり、人と Agent の協働を支える
コンテキストランタイムです。共同で進めた作業を、理解・引き継ぎ・継続が可能なプロジェクトコンテキストとして
蓄積します。

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

| 指標 | PowerContext | [PowerMem](https://www.powermem.ai/benchmark) | フルコンテキストのベースライン |
| --- | ---: | ---: | ---: |
| 精度 | **90.78%**（1,398/1,540） | 87.79% | 52.9% |
| 検索 p95 レイテンシ | **1.38 秒** | 1.44 秒 | 17.12 秒 |
| 質問ごとの回答 token 数 | **約 1.65k** | 約 0.9k | 26k |

PowerContext の結果は、このベンチマークの全 10 会話に含まれる 1,540 問の採点対象すべてを網羅しています。
データセットの選択、検索、judge、レイテンシ、token、Artifact の境界については、
[LoCoMo 評価の詳細](benchmark/locomo/README.md)を参照してください。

### [SWE-bench Pro public v2](https://github.com/scaleapi/SWE-bench_Pro-os)

[SWE-bench Pro public v2](https://github.com/scaleapi/SWE-bench_Pro-os) の全 **731 タスク**を対象としたペア評価では、
PowerContext を有効にすると、タスク解決率が **82.35%** から **86.73%** へと **4.38 ポイント**向上しました。

この評価は Codex 環境で実行し、PowerContext OFF と ON の両グループで `gpt-5.6-sol` モデルを使用しました。

| 結果 | PowerContext OFF | PowerContext ON | 変化 |
| --- | ---: | ---: | ---: |
| 解決済みタスク | 602 / 731 | **634 / 731** | **+32** |
| タスク解決率 | 82.35% | **86.73%** | **+4.38 ポイント** |

[SWE-bench Pro public v2 評価の詳細](evaluation/README.md)。

---

## プラグイン

PowerContext は Codex、Claude Code、DeepSeek Harness 向けの公式プラグインとインストールガイドを提供します。
3 つのインテグレーションはすべて、PowerContext Server を通じて同じスコープ付きデータと履歴を保持する契約を
使用します。プラグインが Server を自動的に起動したり、組み込んだりすることはありません。

### 公式インテグレーション

<table>
<tr>
<td align="center" width="120"><img src="https://github.com/openai.png?size=120" alt="Codex" width="48" height="48" /><br /><sub><b>Codex</b></sub></td>
<td align="center" width="120"><img src="https://github.com/anthropics.png?size=120" alt="Claude Code" width="48" height="48" /><br /><sub><b>Claude Code</b></sub></td>
<td align="center" width="120"><img src="https://github.com/deepseek-ai.png?size=120" alt="DeepSeek Harness" width="48" height="48" /><br /><sub><b>DeepSeek Harness</b></sub></td>
</tr>
</table>

## クイックスタート

macOS または Linux、Python 3.11 以降、[`uv`](https://docs.astral.sh/uv/)、Codex CLI が必要です。

### 1. PowerContext とプラグインをインストールする

```bash
uv tool install "powercontext[cli,server]==0.0.2"

# 1 つ以上のインテグレーションを選択します。
powercontext setup codex --source oceanbase/powercontext --ref v0.0.2
powercontext setup claude-code --source oceanbase/powercontext --ref v0.0.2
powercontext setup dsh --source oceanbase/powercontext --ref v0.0.2
```

最初のコマンドは、隔離された環境に CLI とローカル Server をインストールします。以降の setup コマンドは、
対応するリポジトリの tag から各プラグインをインストールします。既存のインストールを更新するには、setup を
再実行してください。

### 2. ローカル Server を起動して検証する

1 つのターミナルで Server を起動したままにします。

```bash
powercontext server run
```

別のターミナルでサービスとプラグインを検証します。

```bash
powercontext doctor
powercontext doctor codex  # または: claude-code / dsh
```

デフォルトでは、Server は `127.0.0.1:8000` で待ち受け、`/mcp` で Streamable HTTP MCP を公開し、
ローカルの SQLite データベースにデータを永続化します。明示的な Memory 操作には inference provider の設定は
不要です。

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
