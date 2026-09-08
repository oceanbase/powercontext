# Dashboard 开发与验证

Dashboard 是 Server 自带的只读页面，入口为 `/dashboard/home`。它帮助用户确定当前内容范围，阅读交接、记忆、经验与技能，并核对参考内容和模型用量。页面的数据来源是现有 HTTP API；`openapi/powercontext.yaml` 与服务实现共同约束展示行为。

## 页面与数据边界

| 页面 | 数据接口 | 展示规则 |
| --- | --- | --- |
| 范围选择 | `GET /v1/scopes`、`GET /v1/scopes/default`、`GET /v1/scopes/{scope_id}` | 未指定范围时读取服务端默认值；显式空值进入选择页；未知范围返回错误 |
| 首页 | 记忆列表、各 Artifact Family 列表及精确版本、统计 | 各部分独立读取。内容存在与否不决定用量是否存在 |
| 记忆 | `POST /v1/memory/entries/list`、`POST /v1/memory/entries/get` | 正文保留原文与换行；链接包含完整 MemoryCitation |
| 交接 | `GET /v1/scopes/{scope_id}/artifacts/handoff` 及精确版本 | 展示记录的状态与下一步；不把列表顺序解释为时间顺序 |
| 经验与技能 | Experience、Skill Family 列表及精确版本 | 目录分页；经验保留背景、处理方式、结果与经验正文；技能保留指令和检查项 |
| 原始材料 | `GET /v1/scopes/{scope_id}/sources/{source_type}/{source_id}` | 先验证材料属于当前记录，再读取正文；材料失败不影响记录本身 |
| 用量 | `POST /v1/stats` | 采用服务端周期、合计、每日数据、用途和可比较样本统计 |

Dashboard 不调用生成接口，不依赖 Handoff Report 开关，也不以 capabilities 或全局管理权限作为阅读前置条件。保存、审核、生成和分发通过已有工具与接口完成。页面没有这些动作的占位按钮。

HTTP 返回的 Artifact 内容使用 JSON 模式校验。严格领域模型中的 tuple 在 JSON 中是数组，不能用 Python 对象模式直接校验 API 字典。

## Scope 的选择和粒度

- 不带 `scope` 的页面读取服务端默认 scope。点击“默认范围”返回这一入口。
- 选择器仅改变当前页面 URL，不修改默认 scope 或工具绑定。切换范围时清除记录身份和分页游标，详情页回到对应目录。
- 选择项优先显示自身名称，括号中显示可读的祖先路径。父子导航来自 `parent_scope_id`，不推断名称、目录或业务层级。
- 内容页按单个 scope 读取。用量页可明确选择“当前范围”或“包含子范围”，分别对应 `exact` 和 `subtree`。
- `context_references` 是准备上下文时显式引用其他范围的关系，与父子关系不同。父级内容不会自动出现在子级目录，关联范围也不自动计入子树统计。
- 范围列表不可读时，已提供精确 scope 的页面仍尝试读取该 scope。仅获得单条 Artifact 授权时，详情页尝试精确版本读取，不要求查看所属范围的元数据。错误不能变成“暂无内容”；无效引用不能回退到另一条记录。

## 产品语言与数据语义

导航使用“交接”“记忆”“经验与技能”“用量”。业务主题来自 scope 的标题和摘要，正文来自已保存内容。不要添加评估模式、示例说明、功能宣传或没有行动价值的状态提示。

交接表达保存时记录的状态。助手说自己完成了某件事，只能证明这份历史记录包含该说法。回放审核须核对原始会话，避免把用户要求、计划或未结束的检查变成完成结论。

首页可以截短摘要并链接全文。详情不得通过拆句、改写或补写产生接口中没有的标题、结论或来源。较长标题降低字号以保持阅读比例，完整正文仍可见。

用量必须区分零、缺报、没有可比较记录和读取失败。`null` 显示为未报告，不参与客户端补算。用途未知时保留服务端用途名称。估算比例仅来自可比较记录的 baseline 和 reduction；负差值表达为增加。模型输入、输出与向量用量分开列出，估算差值不代表账单节省。

每日图与明细表使用同一响应。UTC 日期来自统计接口，不使用浏览器日期补齐历史。首页用量摘要与用量详情共用呈现函数，不另造一组指标。

## 实现职责

代码位于 `src/powercontext/server/dashboard/`。

| 文件 | 职责 |
| --- | --- |
| `routes.py` | URL、scope、精确引用、页面与片段响应 |
| `api.py` | 带当前凭据调用既有 HTTP API、内容校验和稳定读取错误 |
| `content.py` | 每页所需数据的加载与独立失败状态 |
| `presenters.py` | API 字段到模板视图的转换 |
| `session.py` | 浏览器凭据传输和登录恢复 |
| `templates/components/` | 导航、标题、阅读布局、材料面板、图表与错误组件 |
| `labels.json` | 产品界面文案，不包含业务数据 |
| `static/` | 全局版式变量、固定版本资源和许可证 |

页面通过进程内 HTTP transport 调用同一个 Server，转发当前请求凭据，继续执行身份认证与授权。不得从模板读取 runtime、绕过授权取数据库内容或自动使用部署管理员的 token。

浏览器登录把用户提交的 Bearer 凭据保存在仅对 `/dashboard` 生效的 HttpOnly、SameSite Strict cookie 中；HTTPS 下设置 Secure。会话提交检查同源请求。API 不把该 cookie 当作自己的认证入口。页面响应为 `no-store`，HTMX 历史缓存关闭；历史恢复重新读取服务端。

## 组件与视觉基线

Tabler 已有的组件直接复用：导航、面包屑、卡片、列表、表单、按钮、Accordion、Offcanvas、Alert、Spinner 和 Table。图表使用其 ApexCharts 集成。HTMX 负责导航和片段替换；Surreal 只承担框架之间必要的事件连接。

资源固定为 Tabler Core 1.4.0、Tabler Icons 3.31.0、HTMX 2.0.4、ApexCharts 3.54.1。Surreal 1.3.4 固定到 `cd8f18d34067e073d0aa25675cc0649e304292a3`，css-scope-inline 1.1.0 固定到 `14e835ebe3b8596d0f3ee456162edf63bddc95ba`。许可证与资源一同打包。

按 [Surreal 文档](https://github.com/gnat/surreal) 使用脚本所在组件的 `me()`、`on()` 和 `off()`；按 [css-scope-inline 文档](https://github.com/gnat/css-scope-inline) 将 `<style>` 放在组件根节点内，以 `me` 指向所属组件。不要自行解释选择器或重新实现组件系统。

材料面板使用 Tabler 的 `offcanvas-xxl`：大屏并排阅读，小屏使用原生抽屉和遮罩。跨断点显隐交给 Tabler。只有 HTMX 会提前移除正在关闭的节点时，才通过公开的 `hide`、关闭事件和 `dispose` 完成清理。图表在所属节点移除时销毁，避免重复监听和残留实例。

视觉保留白底、蓝色操作、细边界、左侧导航与正文分屏。全局颜色、边界、侧栏宽度和页边距集中管理；页面只定义自身版式。390、1024、1536 像素宽度均须可读，1399/1400 像素交界须验证材料面板，不允许横向溢出、残留遮罩或滚动锁。

## 本地验证

```bash
uv sync
uv run powercontext server --help
make check
uv run pytest tests/test_dashboard.py tests/test_server.py tests/test_access_http.py
make contract-test
```

启动配置好的 Server 后，使用真实 Chromium 检查全部可读范围、响应式页面、scope 切换、浏览器返回、材料面板和离线恢复：

```bash
npm install --prefix /tmp/dashboard-browser playwright@1.61.1
/tmp/dashboard-browser/node_modules/.bin/playwright install chromium
POWERCONTEXT_BROWSER_URL=http://127.0.0.1:8765 \
POWERCONTEXT_BROWSER_OUTPUT=/tmp/dashboard-verification \
NODE_PATH=/tmp/dashboard-browser/node_modules \
node scripts/dashboard_browser.cjs
```

需要认证时，通过环境变量 `POWERCONTEXT_REPLAY_TOKEN` 提供当前用户凭据。截图和接口日志可能包含工作内容，应写入仓库外的私有目录。

## 真实会话与连续多日回放

先用独立 SQLite 数据库启动 Server，加载本地 provider 配置。模型标识须与 provider 协议一致：Chat Completions 服务使用 `openai-chat:` 前缀，Responses 服务使用相应模型前缀。不要把配置不匹配解释为无数据。

`scripts/dashboard_replay.py` 从已有 Codex JSONL 中读取完整用户与助手消息，排除工具输出、分析和注入的环境规则，按字符预算截取有边界的窗口。它通过内容源、记忆提取、经验生成和交接准备接口生成数据，并记录原文件、行号、摘要和响应。

```bash
uv run python scripts/dashboard_replay.py \
  --output /tmp/private-dashboard-replay \
  --session /path/to/rollout.jsonl \
  --title Dashboard \
  --summary "Dashboard implementation and UI quality" \
  --max-chars 16000
```

经验候选须对照窗口中的原文审核，再通过 revise/approve 接口保存。交接通过 finalize/commit 接口保存。技能须由现有生成接口产生并审核；无法生成时保留失败结果，不用手写技能冒充模型结果。

连续多日实验在独立进程中控制 UTC 时钟，每天关闭并重新打开 Server，使用同一实验数据库。它把选定会话的连续完整消息分成三天，每天先召回前日内容，再保存、提取、召回当天内容并读取统计。时间是实验条件，业务内容与模型结果均通过真实 API 产生。不能将其描述为生产环境实际经过三天。

```bash
uv run python scripts/dashboard_multiday.py \
  --output /tmp/private-dashboard-multiday \
  --session /path/to/rollout.jsonl \
  --last-line 1600 \
  --start-date 2026-09-06 \
  --env-file .env
```

检查次日仍能读取前日引用、1024/8000 字节预算、空子级不继承父级内容，以及每日统计是否落入预定 UTC 日期。进一步使用已有 revise/retire 接口验证修订、失效和历史读取，使用 scope 更新接口验证显式引用与子树统计的区别。所有写入必须经过接口；数据库核对使用只读连接。

回放日志缓存成功请求。请求参数变化须使用另一个输出目录；HTTP 失败保留尝试记录。传输超时意味着结果未知，先通过查询接口核实，再决定是否继续，不能盲目重发可能已成功的写入。

## 消融与验收判据

| 实验 | 必须保留的行为 |
| --- | --- |
| 关闭生成配置和 Handoff Report | 已保存内容仍可读；生成请求明确失败 |
| 仅授权一个 scope | 能读该范围；不显示其他范围内容，不要求全局观察权限 |
| 移除原始材料可读性 | 记录正文仍在；材料错误提供恢复入口 |
| 空范围与仅有记忆 | 各目录独立表达状态，不要求先生成经验或交接 |
| 相同问题分别输入原文、8000 字节、1024 字节上下文 | 记录保留或丢失的约束、下一步和引用；不预设短上下文等效 |
| 泛化续接问题与明确业务词 | 分别记录空召回与命中；不以关键词结果替代泛化问题结果 |
| 次日修订、失效、scope 引用变化 | 当前召回遵循新状态，旧版本仍按精确引用读取 |
| 断网、认证失效、服务失败后恢复 | 错误与无内容不同；恢复后能继续阅读，历史导航与滚动正常 |

单次生成或几个会话只能发现具体失败，不能证明所有问题的召回质量。验收记录应列出服务配置、原始窗口、请求、API 响应、截图、只读数据库核对结果及仍未验证的条件。

## 回放中确认的限制

一次三天连续提取保留了后续请求已经取代的“三页范围”结论。通过现有修订和失效接口纠正后，当前召回不再包含过时条目，23 个历史条目引用仍能返回原文，过期引用写入返回 409。提取成功不能代替对冲突和时效性的审核。

同一续接问题的独立 Codex 回放中，完整会话能说明页面范围和下一步；以 `Tabler` 检索得到的 8000 字节预算上下文只保留技术约束，1024 字节预算进一步减少了约束。消费者明确报告证据不足，没有补写下一步。这里的预算是上限，不是实际返回长度；高精简比例不证明任务信息完整。

这组实验使用真实会话和模型调用，控制的是隔离进程的 UTC 时钟。它验证了连续写入、重启读取、精确引用、跨范围关系及每日统计；没有验证真实运行数日的调度可靠性，也没有证明自然语言召回能覆盖任意问题。
