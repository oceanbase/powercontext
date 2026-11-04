# MiniMax 交互记录

本文件是 MiniMax 参与本任务的完整记录：发了什么、回了什么、用了哪部分、哪部分被拒绝。

## 1. 说明：本机没有 MiniMax 凭据

本次提交所在的机器上没有 `MINIMAX_API_KEY`，也没有安装 MiniMax Code 的命令行（`MINIMAX_DATA_DIR`/`~/.minimax` 不存在）。
因此程序按设计走 **manual（人工粘贴）** 通路：

1. `python run.py agent --step prepare` 生成提示词 `agent/artifacts/after/prompt.md`（12 409 字符），
   内容 = 任务说明 + 上下文模块组装出的规格/代码来源/既有测试/环境实测；
2. 操作者把提示词交给 MiniMax，把回答存成文件，设 `MINIMAX_AGENT_OUTPUT=<文件>` 后再跑同一条命令；
3. 程序从回答中抽取 `diff --git` 补丁、校验、写入 `agent/artifacts/after/proposal.patch`，并把
   后端、模型、提示词摘要、回答摘要写进 seekdb 的 `session_log`（`role='minimax'`）。

要换成真实 MiniMax，只需设置 `MINIMAX_API_KEY`（或 `MINIMAX_AGENT_CMD`），其余流程不变。

## 2. 发给 MiniMax 的内容（摘要）

| 部分 | 来源 | 说明 |
| --- | --- | --- |
| 任务说明 | `agent/instruction.md` | 要改什么（ARR-001/002/003/004/005/005a/005b）、不能改什么（ARR-006/007） |
| 适用规格 | seekdb 表 `spec` | 9 条规格的陈述、观测证据、复现 SQL |
| 代码来源 | `spec_source` + `SEEKDB_REPO` 实时读取 | 路径、符号、行号、原文摘要、内容哈希 |
| 既有测试 | `tools/deploy/mysql_test/test_suite/array/t/*.test` | 必须继续通过的回归文件及其哈希 |
| 环境实测 | guard 写入 `session_log` 的逐条 SQL 结果 | 例如 `insert into tc_insert(id, slots[2], note) values (2, 42, 'target')` → `error 1064` |

完整提示词：`agent/artifacts/after/prompt.md`；上下文正文：`agent/artifacts/after/context.md`。

## 3. 回答与采用情况

| 阶段 | 后端 | 模型 | 回答 | 采用情况 |
| --- | --- | --- | --- | --- |
| after（第 1 次，未设置 `MINIMAX_AGENT_OUTPUT`） | manual | — | 无（程序明确报告 `MINIMAX_AGENT_OUTPUT is not set`，退出码 2） | 未采用：没有回答就不产出补丁，也不保存"已完成"状态 |
| after（第 2 次，`tests/fixtures/smoke_answer.md`） | manual | — | 单个文件的 unified diff（6 行新增） | 采用：补丁通过 `git apply` 应用到 seekdb 检出，产生的代码差异见 `agent/code/minimax-change.diff` |

> `tests/fixtures/smoke_answer.md` 是为打通"生成—校验—应用"链路而准备的最小回答（文件开头已注明
> "Hand-written answer for the smoke test (not a MiniMax answer)"）。它只新增一条文法产生式
> `column_ref '[' expr ']'`，用于证明**验收链路有效**：应用该补丁后 `grammar_check` 从
> `element-access node: none` 变为 `element-access node: T_OP_ARRAY_INDEX`。
>
> **它不构成完整功能**：解析器之外的节点定义、解析路径、表达式实现与回归结果都未完成，
> 因此真实服务层的 4 个验收项在打补丁后仍然失败，guard 如实报告
> `acceptance 1/5 pass, compatibility 2/2 pass`，并没有把这个半成品当作通过。

## 4. 使用记录（可复核）

`session_log` 中与 MiniMax 相关的行：

```bash
python run.py state log | findstr minimax
```

每条记录包含：`backend`、`model`、`prompt_chars`、`prompt_sha`、`output_chars`、`output_sha`、`ok`、`error`。
回答正文本身保存在 `agent/artifacts/<stage>/answer.md`，补丁保存在同目录的 `proposal.patch` 与
seekdb 的 `artifact` 表（按 任务 + 阶段 + 摘要 寻址），两者内容一致。

## 5. 换成真实 MiniMax 的步骤

```bash
export MINIMAX_API_KEY=...            # 或 export MINIMAX_AGENT_CMD="minimax code --prompt"
export MINIMAX_MODEL=MiniMax-Text-01  # 按账号可用模型调整
python run.py agent --step prepare
python run.py state history           # 新进程
python run.py agent --step apply --worktree <seekdb 检出>
```

若 API 返回错误，程序不会退化为"自己写补丁"：它记录失败原因并以非零码退出，等待人工处理
（`context/minimax.py` 中的 `dispatch()`）。
