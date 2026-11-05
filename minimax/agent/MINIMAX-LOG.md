# MiniMax 交互记录

本文件记录 MiniMax 如何参与本任务：发了什么、回了什么、用了哪部分、哪部分被系统拒绝。

## 1. 实际采用的接入方式与凭据

| 项 | 值 |
| --- | --- |
| 接入方式 | HTTPS ChatCompletion（`context/minimax.py::run_api`） |
| 端点 | `POST https://api.minimaxi.com/v1/chat/completions`（本机实测：无效 key 返回 401 `authorized_error (1004)`） |
| 模型 | `MiniMax-M3.1-Flash` |
| 凭据来源 | `~/.mmx/config.json`（MiniMax CLI 已登录的 M Plan 凭据）。优先级：`MINIMAX_API_KEY` → `~/.minimax_key` → mmx 配置 |
| 额度 | `mmx quota show`：`model_name=general`，当期剩余 97%，当周剩余 99% |
| 温度 / 上限 | `MINIMAX_TEMPERATURE`（聚焦请求用 0）、`MINIMAX_MAX_TOKENS` 8192–32768 |

> 另有一把 key（`sk-api-…`，126 字符，写在本机 `~/.minimax_key`）对同一端点返回
> `HTTP 402 insufficient_balance_error (1008)`，即该 key 所属账号无可用额度。系统因此优先使用
> mmx 已登录的凭据，并在运行记录里写明 `key_source`（绝不记录 key 本身）。

## 2. 发送内容

| 部分 | 来源 | 说明 |
| --- | --- | --- |
| 输出契约 | `minimax.SYSTEM_PROMPT` + `OUTPUT_CONTRACT` | 明确"无工具、无文件系统，只输出 unified diff"，禁止 shell 命令与 XML |
| 适用规格 | seekdb 表 `spec` | 规格陈述、实测证据、复现 SQL |
| 代码位置 | `spec_source` + `SEEKDB_REPO` 实时读取 | 路径、符号、行号、**逐字源码窗口**（`MINIMAX_SNIPPET_LINES`，默认 6 行） |
| 既有测试 | `tools/deploy/mysql_test/test_suite/array/t/*.test` | 必须继续通过的回归文件及哈希 |
| 环境实测 | guard 写入 `session_log` 的逐条 SQL 结果 | 例如元素级 INSERT → `error 1064` |

## 3. 调用记录（真实调用，可在 `session_log` 复核）

| # | 范围 | 提示词 | 结果 | 系统处理 |
| --- | --- | --- | --- | --- |
| 1 | 全任务 9 条规格 | 11 474 字符 | `ok`，回答只有 `diff --git a/placeholder b/placeholder`，称"没有文件系统工具" | **拒绝**：路径不在允许范围 |
| 2 | 全任务 + 源码窗口 | 17 410 字符 | 回答是 shell 片段（`find . -maxdepth 3 …`） | **拒绝**：没有 `diff --git` 块 |
| 3 | 全任务（收紧契约） | 18 208 字符 | 空消息，`finish_reason=length`（`max_tokens` 被推理 token 用尽） | 记录 `reasoning_tokens`，不产生补丁 |
| 4 | 聚焦 ARR-005 | 7 198 字符 | 两文件真实 diff：`ob_item_type.h` 加枚举、文法加产生式 | `git apply` 拒绝：hunk 计数与内容不符（`@@ -3171,8 +3171,12 @@` 只有 4 行上下文） |
| 5 | 聚焦（源码逐字） | 7 3xx 字符 | 同上两文件 | `git apply` 拒绝：上下文行 `T_FUN_SYS_LAMBDA` vs 文件 `T_FUNC_SYS_LAMBDA`（差一个字符） |
| 6 | 聚焦（`temperature=0` + 强化"逐字复制"） | 同上 | 同上两文件，仍是 `T_FUN_SYS_LAMBDA` | 同上拒绝（模型确定性错误） |

结论：

* MiniMax **被真实调用**（有 request_id、token 用量、`session_log` 记录），并给出了正确方向：
  在 `ob_item_type.h` 增枚举、在文法增 `column_ref '[' expr ']'` 产生式；
* 但它的补丁**不能直接用**：hunk 行数不自洽、上下文行有一个字符的转录错误，严格校验下无法应用；
* 系统按设计拒绝这些补丁，没有把"模型回答"伪装成"已改好"；最终提交的代码改动是作者据此复核/修订后的版本，
  修改前后对照见 `agent/snapshots/` 与 `agent/code/`。

## 4. 由这几次调用得到的产品改进（均已并入代码）

1. 源码窗口**逐字输出**（此前加了缩进，模型抄进 hunk 后 `git apply` 失败）；
2. 补丁缺 `---`/`+++` 时自动补齐（`patchutil.normalize`）；
3. 路径白名单校验（`src/sql/`、`src/query/`、`tools/deploy/mysql_test/test_suite/array/`），拦截 `placeholder` 这类越界文件；
4. 空回答诊断（`finish_reason`、`reasoning_tokens`），`MINIMAX_MAX_TOKENS` 默认提到 32768；
5. 记录 `key_source`（env / 文件 / mmx 配置），便于复核用了哪把凭据；
6. `run.py agent --spec <ID>`：把大任务拆成聚焦请求，控制单次调用的推理预算与耗时。

## 5. 复跑命令

```powershell
$env:SEEKDB_REPO="D:\code\oceanbase\seekdb"
$env:MINIMAX_MODEL="MiniMax-M3.1-Flash"

# 聚焦请求（推荐，单次几十秒）
python run.py agent --step prepare --stage focus --spec ARR-005 --instruction agent\instruction-focus.md

# 新进程：读状态、应用、验证
python run.py state history
python run.py agent --step apply --stage focus --worktree D:\code\seekdb
python run.py state log | findstr minimax
```
