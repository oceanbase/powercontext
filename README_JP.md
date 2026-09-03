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

## 利用中の Agent と接続する

[PowerContext](https://pypi.org/project/powercontext/) をインストールします：

```bash
uv tool install "powercontext[cli,server]==0.1.0"

# To use the latest unreleased code instead:
# uv tool install "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@master"
```

別のターミナルでローカル Server を起動します：

```bash
powercontext server run
```

Server はデフォルトで、コンテキストをローカルの SQLite データベースに保存します。

次に Agent との連携を設定します。例：

```bash
powercontext setup codex --ref v0.1.0  # --ref also accepts a Git commit, such as 55616dca.
```

その他の Agent の設定方法と導入方法は、[Agent セットアップガイド](https://powercontext.oceanbase.io/en/docs/tutorials/agent-quickstart/)を参照してください。対応する Agent クライアントと IDE は MCP または専用の連携機能で接続できます。

<table>
<tr>
<td align="center" width="120"><a href="docs/en/docs/how-to/configure-codex.md"><img src="https://raw.githubusercontent.com/lobehub/lobe-icons/refs/heads/master/packages/static-png/light/codex-color.png?size=120" alt="Codex" width="48" height="48" /><br /><sub><b>Codex</b></sub></a></td>
<td align="center" width="120"><a href="docs/en/docs/how-to/configure-claude-code.md"><img src="https://raw.githubusercontent.com/lobehub/lobe-icons/refs/heads/master/packages/static-png/light/claudecode-color.png?size=120" alt="Claude Code" width="48" height="48" /><br /><sub><b>Claude Code</b></sub></a></td>
<td align="center" width="120"><a href="docs/en/docs/how-to/configure-dsh.md"><img src="https://raw.githubusercontent.com/lobehub/lobe-icons/refs/heads/master/packages/static-png/light/deepseek-color.png?size=120" alt="DeepSeek Harness" width="48" height="48" /><br /><sub><b>DeepSeek Harness</b></sub></a></td>
<td align="center" width="120"><a href="integrations/hermes/README.md"><picture><source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/lobehub/lobe-icons/refs/heads/master/packages/static-png/dark/hermesagent.png?raw=true&size=120"><img src="https://raw.githubusercontent.com/lobehub/lobe-icons/refs/heads/master/packages/static-png/light/hermesagent.png?raw=true&size=120" alt="Hermes Agent" width="48" height="48" /></picture><br /><sub><b>Hermes Agent</b></sub></a></td>
<td align="center" width="120"><a href="docs/en/docs/how-to/configure-pi.md"><picture><source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/lobehub/lobe-icons/refs/heads/master/packages/static-png/dark/pi.png?size=120"><img src="https://raw.githubusercontent.com/lobehub/lobe-icons/refs/heads/master/packages/static-png/light/pi.png?size=120" alt="Pi Coding Agent" width="48" height="48" /></picture><br /><sub><b>Pi Coding Agent</b></sub></a></td>
<td align="center" width="120"><a href="docs/en/docs/how-to/configure-openclaw.md"><img src="https://raw.githubusercontent.com/lobehub/lobe-icons/refs/heads/master/packages/static-png/light/openclaw-color.png?size=120" alt="OpenClaw" width="48" height="48" /><br /><sub><b>OpenClaw</b></sub></a></td>
</tr>
<tr>
<td align="center" width="120"><a href="docs/en/docs/how-to/configure-opencode.md"><picture><source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/lobehub/lobe-icons/refs/heads/master/packages/static-png/dark/opencode.png?size=120"><img src="https://raw.githubusercontent.com/lobehub/lobe-icons/refs/heads/master/packages/static-png/light/opencode.png?size=120" alt="OpenCode" width="48" height="48" /></picture><br /><sub><b>OpenCode</b></sub></a></td>
<td align="center" width="120"><a href="integrations/workbuddy/README.md"><img src="https://thesvg.org/icons/workbuddy/default.svg?size=120" alt="WorkBuddy" width="48" height="48" /><br /><sub><b>WorkBuddy</b></sub></a></td>
<td align="center" width="120"><a href="integrations/bub/README.md"><img src="https://github.com/bubbuild.png?size=120" alt="Bub" width="48" height="48" /><br /><sub><b>Bub</b></sub></a></td>
<td align="center" width="120"><a href="docs/en/docs/how-to/configure-pydantic-ai.md"><img src="https://thesvg.org/icons/pydantic/default.svg?size=120" alt="Pydantic AI" width="48" height="48" /><br /><sub><b>Pydantic AI</b></sub></a></td>
<td align="center" width="120"><a href="docs/en/docs/how-to/configure-langchain.md"><img src="https://raw.githubusercontent.com/lobehub/lobe-icons/refs/heads/master/packages/static-png/light/langchain-color.png?size=120" alt="LangChain" width="48" height="48" /><br /><sub><b>LangChain</b></sub></a></td>
<td align="center" width="120"><a href="docs/en/docs/how-to/configure-langgraph.md"><picture><source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/lobehub/lobe-icons/refs/heads/master/packages/static-png/dark/langgraph.png?size=120"><img src="https://raw.githubusercontent.com/lobehub/lobe-icons/refs/heads/master/packages/static-png/light/langgraph.png?size=120" alt="LangGraph" width="48" height="48" /></picture><br /><sub><b>LangGraph</b></sub></a></td>
</tr>
</table>

アプリケーションは、非同期 Python クライアント、HTTP API、MCP、または同一プロセス内の Core SDK から PowerContext を利用できます。入口を選ぶには[インターフェースリファレンス](https://powercontext.oceanbase.io/en/docs/reference/interfaces/)を参照してください。

## PowerContext で何が変わるか

![LoCoMo と SWE-bench Pro における PowerContext の結果をまとめた比較図](docs/assets/readme-benchmark-summary.svg)

比較に用いた評価方法、詳細な結果、適用範囲は[公式ベンチマークページ](https://powercontext.oceanbase.io/en/benchmarks/)を参照してください。

## PowerContext を開発する

```bash
make install
make check
make test
```

開発ワークフロー全体については [CONTRIBUTING.md](CONTRIBUTING.md) を参照してください。

## さらに詳しく

- [コアコンセプト](https://powercontext.oceanbase.io/en/docs/explanation/core-concepts/)
- [Memory と Handoff](https://powercontext.oceanbase.io/en/docs/explanation/memory-and-handoff/)
- [Experience と Skill のライフサイクル](https://powercontext.oceanbase.io/en/docs/explanation/experience-and-skill-lifecycle/)

PowerContext は [PowerMem](https://www.powermem.ai/) の後継プロジェクトです。

## ライセンス

PowerContext は [Apache License 2.0](LICENSE) のもとで提供されています。
