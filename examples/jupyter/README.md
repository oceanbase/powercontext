# 用 Python 一步步体验 PowerContext

这套教程从开发订单 CSV 导入器开始：保存项目约定，维护变化的规则，为当前问题准备上下文，
交接尚未完成的工作，再把验证过的做法保存为 Experience 和 Skill，最后接入真实 Agent。

每篇都有场景说明、可执行代码、输出解读和练习。核心调用直接留在单元格里，
可以改一个输入，再运行观察变化。共 22 篇：11 篇基础与 HTTP 教程、10 篇进阶实验，以及 1 篇完整团队工作流。前七篇不需要模型或 API Key。
想先看完整过程，可以直接打开 [22 · 从接手到团队复用](22_complete_team_workflow.ipynb)。
下文给出课程的接口选择、功能覆盖和成功标准。

## 先运行第一篇

基础篇需要 Python 3.11+；完整课程（含 OpenDAL）需要 Python 3.12+、`uv` 和这个仓库的完整 checkout。在**仓库根目录的终端**执行：

```bash
uv sync --locked --group notebooks
uv run --locked --group notebooks jupyter lab --notebook-dir=examples/jupyter
```

在浏览器打开终端显示的 Jupyter 地址，选择 `01_memory_across_sessions.ipynb`，
Kernel 选择 **Python 3 (ipykernel)**，用 **Shift+Enter** 逐格运行。
安装完成后，也可以在仓库根目录用 `make notebooks` 启动。

01–10、12–22 的首个代码单元会启动真实本地 Server，监听随机回环端口，默认使用独立 SQLite 数据库。
每篇都会准备自己的 Scope 和材料，不依赖上一篇的运行结果。数据库放在本目录 `.powercontext/` 下，
环境初始化不会连接已有业务数据库。第一次安装需要访问软件包源，前七篇在安装后可以离线运行。

第一篇运行成功时，你会看到：

1. 新项目最初没有可用上下文。
2. 保存金额约定后，`prepare_context` 返回含该约定的文本与引用，可直接加入模型消息。
3. 新建 Client 连接仍能读到这条知识。
4. 重启 Server 后，同一制品的历史版本仍然可读。
5. 最后一个单元格关闭本篇 Server。

## 使用 OceanBase 运行

01–10、12–22 的代码也可以连接 OceanBase MySQL 模式。先准备一个专供本套教程使用的测试数据库，
然后在 `examples/jupyter/.env` 或启动 Jupyter 的环境中配置 `POWERCONTEXT_NOTEBOOK_OCEANBASE_URL`。
连接格式为 `mysql+aoceanbase://user:encoded-password@host:2881/powercontext_notebooks`，密码中的特殊字符需要 URL 编码。
模板中有该配置项；不要把实际连接串写进单元格。

```bash
make notebooks
# 配置真实模型后，执行模型篇及默认篇
make notebooks-test ARGS="--with-models"
```

初始化表格会显示实际后端 `oceanbase`。每次实验的幂等键带有本次运行标识，完整重跑会创建新的 Scope；
同一次实验中重复发送相同创建请求仍保持幂等。Server 重启后继续连接同一测试库。
不设置该变量时保持 SQLite；辅助代码不会自动继承 `POWERCONTEXT_SERVER_DATABASE_URL` 连接业务库。

OceanBase 测试数据会保留在专用数据库中，关闭 Kernel 或清理本地目录不会删除它。
第 11 篇使用独立 Server，需要将该 Server 的 `POWERCONTEXT_SERVER_DATABASE_KIND` 设为 `oceanbase`，
并将 `POWERCONTEXT_SERVER_DATABASE_URL` 指向专用测试库；HTTP Notebook 的业务请求无需改变。
可复制 [OceanBase Server 模板](server-http-oceanbase.env.example) 到本目录 `.powercontext/http-oceanbase.env`，
填入连接串，再将 HTTP 启动命令的 `--env-file` 指向这个文件。

## 学习路径

建议顺序学习，也可以直接打开感兴趣的一篇。表中时间不包含首次安装和模型等待。

| Notebook | 要解决的问题 | 主要体验 | 时间 | 模型 |
| --- | --- | --- | --- | --- |
| [01 · 让新会话用上项目约定](01_memory_across_sessions.ipynb) | 新会话如何知道已经确认的决定？ | Memory 写入、上下文准备、新连接与重启后读取 | 10 分钟 | 无 |
| [02 · 项目规则变了，记忆怎么办](02_memory_lifecycle.ipynb) | 怎样修订和停用过期知识？ | 按条维护、精确引用、冲突与历史版本 | 15 分钟 | 无 |
| [03 · 同时做两个项目，如何避免串台](03_scopes.ipynb) | 相同问题在不同项目如何回答？ | Scope 隔离、父子层级、幂等创建 | 10 分钟 | 无 |
| [04 · 一次提问需要多少上下文](04_prepared_context.ipynb) | 怎样只给模型本次需要的材料？ | query、UTF-8 字节预算、引用和模型消息 | 15 分钟 | 无 |
| [05 · 工作做到一半，交给另一位接手](05_work_handoff.ipynb) | 接收方怎样知道从哪里继续？ | 真实检查、预览、commit、接收回执与 partial 结果 | 25 分钟 | 无 |
| [06 · 一次修复怎样变成可复用经验](06_reviewed_experience.ipynb) | 已确认和待确认的经验怎么处理？ | 直接创建 Experience、提议更新、候选修订、并发审核、召回 | 20 分钟 | 无 |
| [07 · 把操作步骤交付为 Skill](07_managed_skill.ipynb) | 怎样获得真正可用的 Skill 文件？ | 直接创建、标准包导出、审核更新、历史与 deprecated | 20 分钟 | 无 |
| [08 · 从工作材料生成记忆、经验和 Skill](08_automatic_extraction.ipynb) | 怎样从材料中生成长期知识？ | 自动提取、同主题修订、来源、真实模型生成候选 | 25 分钟 | Generation |
| [09 · 换一种说法，还能找到吗](09_search_comparison.ipynb) | 关键词不同时怎样检索？ | FTS / Vector / Hybrid、LLM rerank、排名与耗时 | 20 分钟 | Embedding + Generation |
| [10 · 给真实 Agent 接上 PowerContext](10_agent_memory.ipynb) | 模型是否真的收到了长期知识？ | Middleware、模型输入、binding、auto_capture、主动记忆工具 | 25 分钟 | 对话模型 |
| [11 · 用 HTTP 管理 Source 与四类制品](11_http_api_lifecycle.ipynb) | 独立应用怎样调用 Server？ | 普通 JSON、统一 API、分页、历史、ETag 和召回范围 | 25 分钟 | 无；需单独启动 Server |

01–07 使用可检查的显式内容。06–07 先直接创建制品，再为原制品提出更新候选，观察审核前后的差别。
08–10 才调用真实模型；第 10 篇先使用 Middleware，再使用主动记忆工具；完整执行需要模型支持工具调用。

## 进阶实验与完整展示

每篇独立创建 Scope 和材料，关键公开 API 留在单元格中。支持进程的完整源码位于 [support](support/)。

| Notebook | 具体场景与成功证据 | 依赖 |
| --- | --- | --- |
| [12 · 团队知识共享](12_team_knowledge_sharing.ipynb) | 隔离、显式上下文引用、准确版本发布；修改原件后比较接收方内容 | 无模型 |
| [13 · 权限与审核分工](13_team_access_control.ipynb) | 不同真实身份的提交、越权拒绝、批准、只读分享、撤销和审计 | 无模型 |
| [14 · 持续接入项目文档](14_continuous_source_ingestion.ipynb) | OpenDAL 文件扫描、增量快照、坏文件拒绝、checkpoint 与恢复 | Python 3.12+ |
| [15 · 后台持续沉淀](15_background_learning.ipynb) | 定时提取 Memory、孵化 Experience；重启不重复，候选仍待审 | Generation |
| [16 · Skill 使用反馈](16_skill_feedback_loop.ipynb) | Experience 生成 Skill、真实 Agent 失败、usage 改进、审核与再次执行 | Generation + 工具调用模型 |
| [17 · 外部 Skill 与完整包](17_external_skill_packages.ipynb) | 扫描指纹、原样导入、逐文件校验、外部变更、真实模型 fork | Generation |
| [18 · Skill 分发](18_skill_distribution.ipynb) | 本机托管发布、独立 Receiver 安装、完整包审核更新、实际回执、冲突和撤回 | 无模型 |
| [19 · MCP 与 Agent 工具](19_mcp_agent_tools.ipynb) | 独立 MCP 进程、真实工具发现和调用；Agent 主动搜索和记忆 | 工具调用模型 |
| [20 · 团队审核与报告](20_team_dashboard_reports.ipynb) | Chromium 实际批准候选、Skill Library、项目范围选择、可见报告与下载、JSON/Markdown 摘要 | Chromium |
| [21 · 观测与故障恢复](21_observability_recovery.ipynb) | Metrics、真实 trace、模型连接失败后恢复；Agent 区分空结果与不可用 | Generation + 工具调用模型 |
| [22 · 完整团队工作流](22_complete_team_workflow.ipynb) | 三次独立 Agent 执行、准确交接、真实修复和测试、审核、Receiver 安装、使用记录、团队共享 | Generation + 工具调用模型 |

12–22 通常每篇需要 15–30 分钟交互学习；22 的自动执行时间主要取决于模型。
15 的调度会处理连接数据库中的可用材料，因此请使用独立数据库，或使用下文的临时库验证器。
18、22 的 Receiver 使用本机独立进程和标准 Agent 目录，不代表已在另一台机器完成网络验收。
16、22 的 Agent 使用真实模型与工具，能修改本次项目的 `amount.py`；测试文件由课程提供并验证不被改写。

20 需要先安装浏览器：

```bash
uv run --locked --group notebooks playwright install chromium
make notebooks-test ARGS="--with-browser --only 20"
```

已有兼容 Chromium 时，可显式设置 `POWERCONTEXT_NOTEBOOK_BROWSER_EXECUTABLE=/absolute/path/to/chrome`。
页面操作截图保存在该篇实验目录；浏览器操作结束后会自动关闭。

附录 [A1 · 扩展 Source、Artifact 与 Trigger](A1_custom_components.ipynb) 面向组件开发者，用实际存储演示自定义材料、制品和纯策略；不计入 22 篇产品主线。可运行 `make notebooks-test ARGS="--only A1"`。

## 这些示例怎样选择接口

| 要做的事情 | 优先使用 |
| --- | --- |
| 保存、搜索和修订日常记忆 | `remember_memory`、`search_memory`、按 citation 的修订和停用操作 |
| 让本次请求用上已有知识 | `prepare_context`；Agent 可直接接入 `PowerContextMiddleware` |
| 保存原始材料或直接提交明确内容 | `create_source`、统一的 `create_artifact` |
| 按制品身份查看内容和版本 | `get_artifact`、`list_artifacts`、`get_artifact_revision` |
| 审核尚待判断的内容 | proposal / generation → 检查 Candidate → approve / reject |
| 独立应用接入与并发更新 | HTTP 专题中的 JSON 请求、Server ETag 与 `If-Match` |
| 把外部身份对应到项目 | `set_scope_binding` / `resolve_scope_binding`；再把解析出的 Scope 交给 Middleware |

当前日常 Memory 召回读取 Scope 的默认 Memory。统一 `create_artifact(family="memory")` 可以创建
独立 Memory 制品，但不会自动让它加入这条召回路径。01–04 保留日常 Memory 操作；
01–02 同时用统一 API 查看其制品和版本。第 11 篇通过实际搜索对照展示这个边界。

Experience、Skill 和 Handoff 使用统一 API 直接创建；Candidate 是待确认内容的审核流程。
Handoff 的主路径是预览再 `commit_handoff`；统一 `create_artifact` 只在内容已经写好时使用。
当前 Python Client 未在制品读取结果中暴露 ETag，所以完整的条件替换流程在第 11 篇用 HTTP 演示。
进阶篇的具体入口和成功证据见上方学习路径。

## 本课覆盖与刻意不覆盖

主线覆盖 Source、默认 Memory、PreparedContext、Experience / Skill 审核、工作交接闭环、提取与生成、
三种检索和重排、LangChain Middleware、LangGraph Memory 工具以及独立 HTTP 制品契约。
进阶篇继续覆盖团队共享和权限、OpenDAL、后台孵化、Skill 使用反馈、外部完整包、托管发布与 Receiver、
MCP、真实浏览器管理、Metrics/OTel 和故障恢复。附录 A1 补充自定义组件与真实存储。

本课的 Agent 是独立 Python 进程，Receiver 在同机通过真实 HTTP 运行；不包含 Codex / Claude Code
原生插件交互、跨机器网络部署、集群高可用或性能基准。OpenDAL 展示当前 Connector 的实际接入能力，
没有把材料扫描等同于自动生成 Memory。各篇会分别检查来源、状态、版本与实际执行结果。

## 独立 HTTP 专题

第 11 篇只用 `httpx` 和普通 JSON，不需要 PowerContext Python Client，也不需要模型。
它从一个真实金额精度问题的 Source 开始，直接创建四类制品，再读取、分页、更新和检查历史。
每步展示可展开的请求与响应 JSON，以及有用的响应头；不显示 Authorization。

在**仓库根目录的终端 A** 启动教学 Server，并保持运行：

```bash
POWERCONTEXT_HOME="$PWD/examples/jupyter/.powercontext/http-server" \
  uv run --locked --group notebooks powercontext server run \
  --env-file examples/jupyter/server-http.env.example --host 127.0.0.1 --port 8000
```

该配置使用独立 SQLite，不启用模型或后台提取。`--env-file` 会替换继承的 `POWERCONTEXT_SERVER_*` 设置。
在**终端 B** 运行 `make notebooks`，打开第 11 篇。换用其他端口时，同时设置
`POWERCONTEXT_CLIENT_SERVER_URL`。连接已有远程服务时使用 HTTPS，将凭据放在
`POWERCONTEXT_CLIENT_API_TOKEN` 中。调用者需要创建新 Scope 及读写权限。

也可以保持 Server 运行，在终端 B 完整执行并保留输出：

```bash
make notebooks-test ARGS="--with-http --only 11"
```

成功时会观察到四类制品的创建与读取，真实的 `304` 条件读取、`412` 过期版本条件、
`428` 缺少更新条件，以及独立 Memory 和默认 Memory 的搜索差别。
最后一个单元格关闭应用连接；在终端 A 按 **Ctrl+C** 停止 Server。

## 配置模型篇

在仓库根目录复制配置模板，再用编辑器填写。不要把 Key 写进 Notebook：

```bash
cp examples/jupyter/.env.example examples/jupyter/.env
```

模板使用 OpenAI-compatible 协议，模型名和 Embedding 维度应填写自己服务的实际值。
`OPENAI_API_KEY` / `OPENAI_BASE_URL` 表示兼容连接配置，不限定供应商。

| 配置 | 用途 |
| --- | --- |
| `OPENAI_API_KEY`、`OPENAI_BASE_URL` | 服务凭据与 API base URL；本地服务按其要求填写 |
| `POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL` | 08、09、15–17、21–22 的生成模型，例如 `openai-chat:<实际模型名>` |
| `POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_MODEL` | 09 的向量模型，例如 `openai:<实际模型名>` |
| `POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_PROFILE_ID` | 稳定的向量配置标识，更换模型或维度时更换相应标识 |
| `POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_DIMENSION` | 模型实际输出维度 |
| `POWERCONTEXT_NOTEBOOK_CHAT_MODEL` | 10、16、19、21–22 的对话模型名，不带 provider 前缀，需支持工具调用 |

已有可用配置时，可在启动 Jupyter 前指定它的**绝对路径**：

```bash
export POWERCONTEXT_NOTEBOOK_ENV_FILE=/absolute/path/to/your/.env
make notebooks
```

辅助代码只把需要的 inference 设置用于本篇独立 Server，不继承文件里的业务数据库和定时任务；
OceanBase 仅由上面的专用 Notebook 配置项显式启用。
第 10 篇由 Agent 直接调用模型，PowerContext Server 仍不需要模型；未单独配置 chat 模型时，
可从 `openai-chat:` 或 `openai:` generation 配置中读取模型名。

已设置的环境变量优先于 `.env`。修改配置后，重启 Kernel 并从头运行。
配置不足会明确停止，不会用测试模型代替。真实服务调用会产生相应用量：
生成篇实际提取、生成或改进候选；09 计算向量并调用 rerank；Agent 篇会有多轮真实对话和工具调用。
复杂生成可配置 `POWERCONTEXT_SERVER_INFERENCE_GENERATION_TIMEOUT_SECONDS=120`；执行器的 `--timeout` 应大于单次服务超时。

## 读懂结果

Source 是原始材料，保存成功不代表已成为 Memory。Memory 的停用会使 entry 退出 active recall，
历史版本仍可读取。准备上下文、加入模型请求、模型实际采用它，是需要分别观察的三个结果。

Handoff 的准备预览、提交、接收确认和任务完成也是不同状态。05 先预览再 commit，两个 Client 模拟交接角色；
10、16、19、21、22 调用真实 Agent。Skill 的创建或批准不会自动在 Host 中安装、发现或执行它；07 实际导出文件，17 验证完整包，18 与 22 用 Receiver 实际安装和撤回。

06–07 的审核操作针对本篇展示的明确教学材料；08 的真实模型候选保留为 pending，供读者判断。
09 只有三个教学问题，排名和 Hit@3 用于解释本次结果，不构成效果基准。
模型措辞可以变化；关键来源、状态、事实和输入检查失败时，应检查真实输出。

## 完整执行与保留输出

每篇会使用全新 Kernel，保留执行后的 Notebook、HTML 和 `summary.json`。源 Notebook 保持无输出。
执行后的 Notebook、HTML、截图、日志和验收清单属于本地运行产物，请保留在 `.powercontext/` 或仓库外的目录中，不提交到 Git。

```bash
# 不需要模型、浏览器或独立 HTTP 服务的篇目
make notebooks-test

# 配置真实模型后执行模型篇及默认篇
make notebooks-test ARGS="--with-models"

# 选择篇目，指定输出目录
make notebooks-test ARGS="--only 01 05"
make notebooks-test ARGS="--with-models --only 08 09 10 --output-dir /tmp/powercontext-tutorial-results"
```

默认输出在本目录 `.powercontext/executed/`。清单记录源码与代码摘要、单元格数量、实际执行数量和耗时。
`passed` 表示整篇运行通过；`failed` 会保留失败处输出；`not_run` 表示未启用模型、浏览器或独立 HTTP 服务，不能当作通过。
失败返回非零退出码。显式选择模型篇却不加 `--with-models`，或选择 11 却不加 `--with-http`，也返回非零。
全量执行（已单独启动 11 的 Server）：`make notebooks-test ARGS="--with-models --with-http --with-browser --timeout 360"`。
独立 HTTP 专题需要另外启用，默认测试和 `--with-models` 都不会向它的 Server 发请求。

## 在临时 OceanBase 中完整验证

该验证器逐篇创建一个专用数据库，避免调度和权限实验影响其他篇目。第 11 篇另外启动真实 CLI Server，
分别验证无鉴权与 Bearer；每次执行无论成功或失败，都停止 Server、删除临时凭证文件，并仅删除本次成功创建的数据库。

需要具有创建和删除测试库权限的配置。显式执行开关代表允许该脚本创建与清理自己的临时库；它不删除既有数据库。
管理员连接来自所选配置文件中的 `POWERCONTEXT_NOTEBOOK_OCEANBASE_ADMIN_URL`，未设置时使用 `POWERCONTEXT_SERVER_DATABASE_URL`。
模型配置也从该文件读取，具体值不会写入验证清单。

```bash
uv run --locked --group notebooks python examples/jupyter/verify_oceanbase.py \
  --env-file /absolute/path/to/test.env --create-temporary-databases

# 只验收部分篇目；仍各自新建、删除临时数据库
uv run --locked --group notebooks python examples/jupyter/verify_oceanbase.py \
  --env-file /absolute/path/to/test.env --create-temporary-databases --only 14 18
```

输出目录的 `verification.json` 记录每个数据库的创建、执行和删除状态；各篇目录保留执行后的 Notebook、HTML、源码与代码摘要。
`deleted: true` 表示已查询确认数据库不存在。失败不是通过；检查 `returncode`、`execution` 和保留的实际异常。

## 常见问题

| 现象 | 检查方式 |
| --- | --- |
| 找不到包、Jupyter 或适配器 | 重新执行 `uv sync --locked --group notebooks`，确认 Kernel 来自同一环境 |
| 提示事件循环已运行 | 单元格使用顶层 `await`，无需 `asyncio.run()` |
| 首格不能监听端口 | Kernel 和 Server 需要回环 socket；检查运行环境是否允许本地端口 |
| 单独重跑写入或审核格出现冲突 | 使用 Restart Kernel & Run All；精确引用与已经终结的候选不能任意重放 |
| 模型配置缺失或调用失败 | 检查配置路径、模型、兼容地址、维度和服务可达性，然后重启 Kernel |
| 模型没有得到预期结果 | 阅读实际 Source、候选或模型输入；不要手工补写结果掩盖模型行为验收失败 |
| 08 生成 Skill 返回 500，日志提示名称非法 | 生成接口不接收调用方指定名称；检查模型输出的 name 是否为小写英文、数字与单连字符，保留实际失败结果 |
| Skill 导出提示目录已存在 | CLI 不覆盖已有目录；检查导出，或完整重跑建立新实验 |
| Agent 回答没用上知识 | 先看回调捕获的实际模型输入，再区分召回、Scope 选择与模型采用问题 |
| HTTP 连接失败或返回 401 / 403 | 检查独立 Server、地址、token 与权限；Notebook 末尾附状态码说明 |

## 清理实验数据

01–10、12–22 的最后一个单元格关闭 Client 与 Server，数据库、示例文件和 Skill 导出保留以供检查。18、22 会先撤回本次 Receiver 安装并撤销目标凭证。
中途停止时运行最后一格，或关闭 Kernel；仅关闭浏览器标签页不会关闭 Kernel。
11 的 Server 需在独立终端停止。连接已有服务产生的远端 Scope 和历史不会随本地清理删除。

确认不再需要结果并关闭相关 Kernel 后，在**仓库根目录**执行：

```bash
uv run --locked --group notebooks python -c "from pathlib import Path; import shutil; shutil.rmtree(Path('examples/jupyter/.powercontext'), ignore_errors=True)"
```

这只清理本套教程的默认数据目录，保留 `.env`，不处理自定义输出目录。
`.powercontext/`、`.env` 和 `.ipynb_checkpoints/` 均已被仓库忽略。
