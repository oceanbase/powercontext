# MiniMax × OceanBase — 规所依：面向软件持续演进的 SDD 上下文管理系统

本目录是参赛作品。它演示一条完整的 **Spec-Driven Development（SDD）** 工作流：把工程规格、代码与测试来源存进 **seekdb**，把任务进展与验证结果通过 **PowerContext** 保存/复用，由 **MiniMax** 依据组装出的开发上下文参与真实代码修改，并用可复现的验收/兼容检查证明"该改的改了、不该动的没动"。

作品处理的业务工程是 **seekdb**（OceanBase 的单机版 SQL 引擎，C++），任务是对它做一次真实的局部功能修改：

> **任务**：seekdb 的 ARRAY 列不支持按元素访问（`col[i]`）。需要在 `INSERT` / `UPDATE` / `DELETE` / `SELECT` 中支持 1-based 元素访问，且不得改变既有的数组字面量、数组函数与向量检索行为。

---

## 1. 目录结构

```
minimax/
├── README.md                  # 本文件：依赖、配置、初始化、调用入口、已知限制
├── run.py                     # 唯一命令行入口（生成器/验收/上下文/状态/就绪度）
├── serve.py                   # HTTP 接口（FastAPI + uvicorn，挂在 run.py 同一套逻辑上）
├── context/                   # 上下文模块
│   ├── db.py                  # 数据库边界：schema、连接（只从环境变量取配置）
│   ├── powercontext.py        # PowerContext 边界：状态保存 / 恢复 / 再导入 / 历史
│   ├── build_context.py       # 上下文组装：规格 + 代码来源 + 既有测试 + 环境实测
│   ├── harness.py             # 环境执行：对真实 seekdb 跑 SQL 并记录错误码与行
│   ├── scenarios.py           # 验证样例的定义（生成 tests/suite.json）
│   ├── guard.py               # 验收：修改前必红、修改后必绿、兼容项恒绿
│   ├── operations.py          # run.py 与 serve.py 共用的操作
│   ├── minimax.py             # MiniMax 边界：api / cli / manual 三种接入
│   ├── patchutil.py           # 补丁抽取与 `git apply` 封装
│   ├── config.py              # 任务常量
│   └── specs/seed_specs.json  # 规格数据集（导入 seekdb 的源数据）
├── scripts/
│   ├── init_db.py             # 从空环境拉起 seekdb 容器并建库建账号
│   ├── build_specs.py         # 把规格数据集导入 seekdb 并索引源代码
│   └── record_baseline.py     # 记录"修改前"的基准观测（golden oracle）
├── grammar_check/
│   ├── static_check.py        # 语法检查：文法中是否存在 col[i] 节点（before/after）
│   └── array_index_probe.cpp  # 可选的 libobparser 编译期探针
├── tests/
│   ├── suite.json             # 验证样例（任务中列出的 SQL，可复现）
│   ├── golden_baseline.json   # 修改前的基准观测（oracle）
│   ├── fixtures/smoke_answer.md
│   └── test_context.py        # 上下文模块自身的测试（pytest）
├── agent/
│   ├── instruction.md         # 交给 MiniMax 的任务说明（含规格编号）
│   ├── MINIMAX-LOG.md         # MiniMax 实际参与记录
│   └── artifacts/             # 每次调用留下的上下文/提示词/回答/补丁
├── docs/
│   ├── 验证样例.md
│   ├── 代码与测试.md
│   ├── 技术使用记录.md
│   └── 一页技术说明.md
└── logs/                      # 本次提交的运行日志（逐条命令与输出）
```

---

## 2. 依赖版本

| 依赖 | 版本 | 用途 |
| --- | --- | --- |
| Python | 3.10.4（脚本按 3.10+ 编写） | 全部模块 |
| PyMySQL | 2.2.8 | 访问 seekdb / OceanBase |
| requests | 2.34.2 | （可选）HTTP 客户端 |
| fastapi | 0.162.0 | HTTP 接口 |
| uvicorn | 0.54.0 | HTTP 服务 |
| pytest | 8.x | 上下文模块测试 |
| Docker | 29.6.1（Engine 已运行） | 启动 seekdb |
| seekdb 镜像 | `oceanbase/seekdb:1.4.0-100000172026082615` | 被测工程 + 数据库 |
| seekdb 源码 | `https://github.com/oceanbase/seekdb`，提交 `108fe22` | 规格中的代码来源 |

被测数据库版本（`select version()`）：`5.7.25-OceanBase seekdb-v1.4.0.0`。

安装依赖：

```bash
python -m pip install pymysql fastapi uvicorn pytest requests
```

---

## 3. 配置

全部配置走环境变量，代码里没有硬编码的主机和口令：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `MINIMAX_DB_HOST` | `127.0.0.1` | seekdb / OceanBase 地址 |
| `MINIMAX_DB_PORT` | `2883` | 端口 |
| `MINIMAX_DB_USER` / `MINIMAX_DB_PASSWORD` | `minimax_ctx` / `minimax_ctx_dev` | 规格库账号（只读写 `minimax_sdd_ctx`） |
| `MINIMAX_SCENARIO_USER` / `MINIMAX_SCENARIO_PASSWORD` | `minimax_test` / `minimax_test_dev` | 演练库账号（只操作 `minimax_scenario*`） |
| `SEEKDB_REPO` | — | seekdb 源码目录，规格中的代码来源从这里实时读取并计算哈希 |
| `MINIMAX_TASK_ID` | `task-arr-element-dml` | 任务标识 |
| `MINIMAX_API_KEY` | — | MiniMax API Key；也可以不设，改用下面的文件 |
| `MINIMAX_API_KEY_FILE` | `~/.minimax_key` | 未设 `MINIMAX_API_KEY` 时从这个文件读 key（不进 shell 历史） |
| `MINIMAX_MODEL` / `MINIMAX_BASE_URL` | `MiniMax-Text-01` / `https://api.minimaxi.com/v1` | MiniMax API 参数 |
| `MINIMAX_AGENT_CMD` | — | 设置后 MiniMax 走命令行 Agent |
| `MINIMAX_AGENT_OUTPUT` | — | 设置后走"人工粘贴"：从该文件读取 MiniMax 的回答 |
| `MINIMAX_GRAMMAR_CHECK` | `grammar_check/static_check.py` | after 阶段语法检查命令 |

> 口令类变量只用于本地开发；`scripts/init_db.py` 会在新环境里重新创建这两个账号。

---

## 4. 从空环境启动（评测请按此顺序执行）

```bash
cd minimax
export SEEKDB_REPO=/path/to/seekdb          # 规格引用的源码仓库
export PYTHONIOENCODING=utf-8

# 1) 拉起 seekdb（首次会拉镜像），建库 + 建账号，并验证两个账号都能登录
python scripts/init_db.py --reset

# 2) 导入规格数据集（读 src/... 校验行号并写入代码来源哈希）
python scripts/build_specs.py --reset

# 3) 记录"修改前"的基准观测（golden oracle）
python scripts/record_baseline.py

# 4) 上下文模块自测
python -m pytest tests -q
```

第 1 步等价于手动执行：

```bash
docker run -d --name minimax-context-seekdb \
  -e ROOT_PASSWORD=minimax_root_pw -p 2883:2881 \
  oceanbase/seekdb:1.4.0-100000172026082615
docker exec minimax-context-seekdb mysql -h127.0.0.1 -P2881 -uroot -pminimax_root_pw \
  -e "CREATE DATABASE minimax_sdd_ctx"
```

---

## 5. 调用入口

### 5.1 命令行（`run.py`）

> **cmd.exe 用户注意**：`cd D:\code\...` 不会切换盘符，随后 `python run.py` 会报
> `can't open file 'C:\Users\...\run.py'`。用 `cd /d D:\code\oceanbase\powercontext\minimax`，
> 或者直接用本目录的两个启动器（从任意目录都能跑，自动切到本目录）：
>
> ```bat
> rem cmd
> D:\code\oceanbase\powercontext\minimax\run.cmd state history
> D:\code\oceanbase\powercontext\minimax\run.cmd agent --step apply --patch agent\code\author-fix.patch --worktree D:\code\seekdb
> ```
>
> ```powershell
> # PowerShell（脚本被策略拦时加 -ExecutionPolicy Bypass）
> powershell -NoProfile -ExecutionPolicy Bypass -File D:\code\oceanbase\powercontext\minimax\run.ps1 state history
> ```
>
> 两个启动器都支持 `set PYTHON=...` / `$env:PYTHON="..."` 指定解释器。

```bash
python run.py specs                        # 列出规格库
python run.py context                      # 打印开发上下文（--json / --compact / --out）
python run.py context --spec ARR-001 --spec ARR-005
python run.py guard --stage baseline       # 修改前：与基准观测一致，且验收项全红
python run.py guard --stage after          # 修改后：验收项全绿，兼容项恒绿
python run.py agent --step prepare         # 组装上下文 + 调 MiniMax + 保存任务状态
python run.py agent --step apply --worktree <seekdb 检出>   # 读取状态、再导入、应用补丁
python run.py agent --step apply --patch agent/code/author-fix.patch --worktree <检出>
python run.py state show                   # 最新任务状态（来自 seekdb）
python run.py state history                # 状态版本历史
python run.py state import                 # 重启后再导入
python run.py state log                    # 使用记录
python run.py ready                        # 任务就绪度（全部规格 done 才算完成）
```

### 5.2 HTTP 接口（`serve.py`）

```bash
python -m uvicorn serve:app --host 127.0.0.1 --port 8099

curl http://127.0.0.1:8099/health
curl "http://127.0.0.1:8099/context?spec=ARR-001&spec=ARR-005"
curl -X POST http://127.0.0.1:8099/tasks/task-arr-element-dml/import
curl http://127.0.0.1:8099/ready
```

### 5.3 MiniMax 接入（三种方式，运行说明中已注明实际采用的方式与版本）

* 已设置 `MINIMAX_API_KEY`（或存在 `~/.minimax_key`）：POST `{base}/chat/completions`，模型见 `MINIMAX_MODEL`；
* 已设置 `MINIMAX_AGENT_CMD`：以 `prompt.md` 为参数调用该命令，读取标准输出；
* 否则（本机没有 MiniMax Key 时的默认路径）：程序生成 `agent/artifacts/<stage>/prompt.md`，把该提示词粘进 MiniMax Code，再把回答存成文件，设置 `MINIMAX_AGENT_OUTPUT=<文件>` 后重跑 `agent --step prepare`。

三种方式产物一致：回答里必须包含 `diff --git` 补丁，程序只抽取补丁部分并校验。

已验证的 API 形状（本机实测，2026-11-04）：

```
POST https://api.minimaxi.com/v1/chat/completions
Authorization: Bearer <key>
{"model": "...", "messages": [...]}
→ 无效 key 返回 401 {"type":"error","error":{"type":"authorized_error", "... (1004)"}}
```

即 `MINIMAX_BASE_URL` 默认值 `https://api.minimaxi.com/v1` 是对的；`MINIMAX_MODEL` 填你账号控制台里实际可用的模型名（如 `MiniMax-M2`）。该域名本机可直连，无需代理。

接通真实 API 的步骤（详细版见 [`docs/接通MiniMax.md`](docs/接通MiniMax.md)）：

```bash
# 1) 把控制台复制的整串 key 写进文件，避免进入 shell 历史
#    PowerShell: "sk-真实key" | Set-Content -NoNewline -Encoding ascii "$env:USERPROFILE\.minimax_key"
export MINIMAX_MODEL="MiniMax-M2"        # 换成你套餐里的模型名

# 2) 自检：只发 "ping"，确认鉴权通过
python -c "from context import minimax; print(minimax.run_api('ping').to_record())"

# 3) 正式调用：组装上下文 → MiniMax → 抽补丁 → 保存状态
python run.py agent --step prepare
#    产物：agent/artifacts/after/{prompt.md, context.md, answer.md, proposal.patch}

# 4) 新进程里再导入状态并应用补丁
python run.py state history
python run.py agent --step apply --worktree /path/to/seekdb
```

API 出错时程序不会自己写补丁：它记录失败原因并以非零码退出（`context/minimax.py::dispatch`），
避免把"模型没回答"伪装成"已改好"。跑通后请把 `agent/MINIMAX-LOG.md` 与
`docs/技术使用记录.md` 里"人工粘贴通路"的表述改成实际后端与模型。

---

### 5.4 在自建的 seekdb 上验证补丁（需要 Linux 构建机）

本机是 Windows，没有 bazel/g++，所以服务层验收只能停在"未打补丁的 1.4.0 镜像"上。
拿到一台能构建的 Linux 机器后按下面执行，服务层验收才会从红转绿。

**先判断这台机器够不够**（`build.py` 会下载 Bazel 9.3.0 与 `deps/3rd/tools/llvm18`，
所以系统 gcc 版本不重要，**glibc、内存、磁盘、网络**才重要）：

```bash
bash scripts/preflight_linux.sh
```

判据：glibc ≥ 2.28、内存 ≥ 16 GB、磁盘 ≥ 50 GB、能访问 github.com。
CentOS 7（glibc 2.17、9 GB 内存）**不满足**，会直接给出替代方案（换 Ubuntu 22.04 容器 / 走 CI）。
没有 git 的机器用 `bash scripts/apply_without_git.sh <seekdb 目录>` 应用（GNU `patch -p1`，已实测）。

```bash
# 一键：应用补丁 → 构建 → 起实例 → 建库 → 验收（脚本在 scripts/verify_on_linux.sh）
git clone https://github.com/oceanbase/seekdb && cd seekdb && git checkout 108fe22
MINIMAX_WORKTREE=$PWD bash /path/to/minimax/scripts/verify_on_linux.sh

# 手工分步：
# 1) 应用解析层改动（agent/code/author-fix.patch：枚举 + 文法产生式，git apply 干净）
git apply /path/to/minimax/agent/code/author-fix.patch

# 2) 构建（首次 init 会拉依赖，耗时较长）
./build.py init
./build.py build seekdb

# 3) 起一个专用实例（数据目录独立，端口别和已有实例冲突）
./build/bin/src/observer/seekdb --nodaemon \
  --base-dir "$HOME/seekdb-patched" --port 2882 \
  --parameter memory_limit=4G --parameter datafile_size=2G &

# 4) 建库建账号（本地构建的 root 默认空口令）
cd /path/to/powercontext/minimax
python scripts/init_local_instance.py --port 2882

# 5) 对这套构建跑验收：完整实现后应为 acceptance 5/5、compatibility 2/2
SEEKDB_REPO=/path/to/seekdb MINIMAX_DB_PORT=2882 python run.py guard --stage after

# 6) 跑仓库自带回归（mysqltest 会自己管理实例，端口另选）
python3 tools/deploy/mysqltest.py run --base-dir "$HOME/mt-inst" --work-dir "$HOME/mt-out" \
  --port 2883 --test-set vector_index.vector_index_bugfix1
./mysql-test-run.sh array --suite=array --do-test=array_arith_op_mysql
```

`scripts/init_local_instance.py` 也可以直接对任意已运行的 seekdb/OceanBase 使用（幂等，可重复执行）：

```bash
python scripts/init_local_instance.py --port 2881                      # 空口令实例
python scripts/init_local_instance.py --port 2883 --root-password=xxx # 有口令实例
python scripts/init_local_instance.py --port 2882 --reset             # 重建库
```

只建演练库（不建规格库）用 `--no-spec-db`；规格库需要导入时对同一端口执行
`MINIMAX_DB_PORT=2882 python scripts/build_specs.py --reset`。

---

## 6. 典型的一次完整运行（也见 `docs/代码与测试.md`）

```bash
python run.py guard --stage baseline        # 修改前：验收项全红（记录为证据）
python run.py guard --stage after           # 修改前跑同一组检查：失败 → 证明检查有效
python run.py agent --step prepare          # 上下文 → MiniMax → 补丁 + 保存状态
#  —— 到此可以结束进程，评测"重启后仍能读取并使用状态" ——
python run.py state history                 # 新进程：读历史
python run.py agent --step apply --worktree <新检出的 seekdb>   # 再导入状态并应用补丁
python run.py guard --stage after           # 修改后：验收项全绿、兼容项恒绿
python run.py state save --stage after       # 保存最终状态
python run.py state mark --spec ARR-001 --spec ARR-005 --state done
python run.py ready                         # 全部 done 才返回 0
```

### 6.1 一条命令复现整条链（推荐给评审）

```bat
rem 需要：seekdb 已启动、seekdb 检出干净（脚本会 apply 后还原）
D:\code\oceanbase\powercontext\minimax\verify.cmd --worktree D:\code\seekdb
```

输出是一张表，逐项对照作品声明的结果（实测 7/7）：

```
STEP                                   RESULT  DETAIL
spec store loaded                      PASS    9 specs, 20 sources (expect 9/20)
guard --stage baseline                 PASS    baseline reproduced … acceptance cases still red (5)
guard --stage after                    PASS    acceptance 0/5 pass, compatibility 2/2 pass
apply reviewed patch                   PASS    2 files changed, 6 insertions(+)
grammar probe on the worktree          PASS    element-access node: T_OP_ARRAY_INDEX
guard --stage after (patched grammar)  PASS    acceptance 1/5 pass, compatibility 2/2 pass
worktree restored                      PASS    git checkout -- .
```

表里的 PASS 表示"与声明一致"，**不是**"测试全绿"：服务层 4 个验收项要有打补丁的 seekdb 构建才会转绿。
日志见 `logs/22-verify-all.log`。

---

## 7. 数据模型（seekdb 中的表）

| 表 | 作用 |
| --- | --- |
| `spec` | 规格条目：`must-change` / `must-keep`、陈述、证据、复现 SQL、关联检查 |
| `spec_source` | 规格的代码来源：路径、符号、行号、原文、说明 |
| `test_case` | 检查项与基准观测摘要（`oracle_key`） |
| `task` / `task_state` | 任务当前状态快照（带版本号），是"重启后恢复"的唯一来源 |
| `task_state_history` | 每次保存的历史版本，供评审核对 |
| `artifact` | MiniMax 给出的补丁、最终代码差异（按任务+阶段+摘要寻址） |
| `session_log` | 使用记录：MiniMax 调用、验收运行、状态导入（写入原始值，不写口令） |

---

## 8. 已知限制

1. **本机无法构建 seekdb。** Windows 上没有 bazel/g++，seekdb 全量编译也不现实。"代码修改有效"用两层检查替代：语法层（`grammar_check/` 直接读文法源，判断 `col[i]` 是否已产生独立节点）和真实服务层（guard 对运行中的 seekdb 1.4.0 执行同一组 SQL）。**没有在本机编译过 C++ 补丁**，这一点在 `docs/代码与测试.md` 中如实标注。
2. **被测 seekdb 是镜像中的 1.4.0 构建，不是打过补丁的构建**；因此 after 阶段在真实服务层的验收项会保持红色，直到有可用构建。guard 会把这个结果原样报告，不会伪造。
3. **当前改动只到解析层（ARR-005）**：`agent/code/author-fix.patch` 加了文法产生式与 `T_OP_ARRAY_INDEX` 枚举（实测可 `git apply`，语法检查 PASS），但解析路径、执行期实现、元素级 INSERT/UPDATE/DELETE 与回归结果都还没有。下一步用 `agent/instruction-2.md` 继续，再按 5.4 编译验证。
4. **MiniMax 的真实提案未采用**：6 次真实调用（`MiniMax-M3.1-Flash`）给出的补丁因 hunk 计数不自洽与一个字符的上下文转录错误无法应用，记录见 `agent/MINIMAX-LOG.md`；系统按设计拒绝，没有把模型回答当成已完成。
5. **MiniMax 无可用 Key 时走人工粘贴路径**，流程、提示词、补丁校验完全可用；凭据优先级与 402 余额错误的处理见 5.3 与 `docs/接通MiniMax.md`。
6. `array_index_probe.cpp` 需要与 seekdb 构建一致的 `libobparser` 头文件与链接参数，本机未验证编译；默认使用 `static_check.py`。
7. 规格引用的行号对应 seekdb `108fe22`；`build_specs.py` 与上下文组装会校验符号仍在原处，漂移会打印警告而不是静默改号。
8. 口令写在开发脚本中并可用环境变量覆盖，仅适用于本地演示环境。
