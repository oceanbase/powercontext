# PowerContext

人と Agent が作業を引き継ぎ、継続するためのコンテキスト。

[![PyPI version](https://img.shields.io/pypi/v/powercontext)](https://pypi.org/project/powercontext/)
[![License Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Discord](https://img.shields.io/badge/Discord-community-5865F2?logo=discord&logoColor=white)](https://discord.com/invite/74cF8vbNEs)

*[English](README.md) · [中文](README_CN.md) · [日本語](README_JP.md)*

作業を始めた人や Agent が、そのまま最後まで終えるとは限りません。あなたが Agent にタスクを渡し、Agent が途中まで進めた後、あなたや別の誰かが引き継ぐことがあります。そのとき、判断の理由や現在の状態は、その会話に置き去りになりがちです。

PowerContext は、会話をまたいでもコンテキストを作業とともに保持します。あなたが戻ったときは、これまでの経緯を確認して現在の状態から続けられます。新しい Agent も同じところから引き継げます。

![あなたと Agent が作業を引き継ぎ、保存されたコンテキストから継続する流れ](docs/assets/readme-workflow.svg)

[公式サイト](https://powercontext.oceanbase.io/en/) · [ドキュメントを読む](https://powercontext.oceanbase.io/en/docs/)

## 作業の続きをそのまま引き継ぐ

作業を引き継ぐと、確認済みの判断、制約、進捗、根拠、次の手順など、その時点で必要なコンテキストを確認できます。履歴をすべて読み返さずに、そのまま続けることも、別の人や Agent に渡すこともできます。

後から何を残すか、次の担当者に何を渡すかは、あなたが決めます。PowerContext は長く使う情報を Memory として保存し、現在の目標と状態を Handoff にまとめます。再利用できる手順は、Experience または Skill として残せます。PowerContext は各項目を対象となる作業の範囲内に保ち、元の情報源と過去の版を残します。

## はじめる

リリース版と対応する Codex 統合をインストールします。Server は専用ターミナルで起動したままにします。

```bash
uv tool install "powercontext[cli,server]==0.2.0"
powercontext server run
```

別のターミナルで実行します。

```bash
powercontext setup codex --ref powercontext-v0.2.0
```

Python 3.11+ が必要です。macOS と Linux をサポートし、Windows は `experimental` です。
`master`、他のホスト、検証手順は [Quick Start](docs/en/docs/get-started/quickstart.md) と
[インストールガイド](docs/en/docs/get-started/install-and-run.md)を参照してください。パッケージと統合は同じ ref を使います。

## 統合

| 種類 | 統合 | タグ |
| --- | --- | --- |
| Agent Host | Codex | `official` |
| Agent Hosts | Claude Code、DeepSeek Harness、Hermes、OpenClaw、OpenCode、Pi、WorkBuddy | `community` |
| Python Agent フレームワーク | Pydantic AI、LangChain、LangGraph | `community` |
| 評価 | Bub | `evaluation` |

`official` は PowerContext プロジェクトによるメンテナンス、`community` はコミュニティの貢献、
`evaluation` は評価専用を意味します。ホスト提供元の推奨や同一の機能を意味するものではありません。
[統合ガイド](docs/en/docs/integrations/index.md)と[機能一覧](docs/en/docs/integrations/capabilities.md)で
`released`、`master_only`、`experimental` の状態を確認できます。

## ドキュメント

- [コンテキストの管理](docs/en/docs/workflows/index.md)：Memory、Handoff、Experience、Skill、Sources、Scope、Artifact。
- [デプロイと運用](docs/en/docs/operate/index.md)：個人サービス、ログ、メトリクス、Tracing、復旧。
- [開発と API](docs/en/docs/develop/index.md)：HTTP、Python、アプリケーション統合。
- [ベンチマーク](https://powercontext.oceanbase.io/en/benchmarks/)：手法、結果、制約。
- [貢献](CONTRIBUTING.md)。

PowerContext は [PowerMem](https://www.powermem.ai/) の後継プロジェクトです。

## ライセンス

PowerContext は [Apache License 2.0](LICENSE) ライセンスで提供されています。
