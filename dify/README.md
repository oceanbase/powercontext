# 时间回声档案馆

“时间回声档案馆”是一个可重复游玩的中文互动剧情工作流。玩家每轮输入一个决定；Dify 先从 PowerContext 召回这个玩家过去的选择，LLM 让旧选择在新剧情里产生可见后果，再把本轮选择和结果写回 PowerContext。使用同一个 `player_id` 开启下一轮，即使是新的 Dify 请求，也会继承此前时间线。

## 技术分工

- Dify：输入校验、HTTP 调用、历史解析、LLM 剧情生成、结果输出等核心编排。
- PowerContext：按可信 `scope_id` 保存 `decision` 记忆，并在后续运行中返回有界历史上下文。
- LLM：只负责生成剧情；不能选择 `scope_id`，也不能接触 PowerContext token。

工作流顺序：

```text
Start -> 构建召回请求 -> POST /v1/context/prepare -> 解析历史
      -> LLM 生成剧情 -> 构建写入请求 -> POST /v1/memory/remember -> End
```

## 交付文件

- `time-echo-archive.yml`：Dify DSL 0.3.0，直接导入。
- `.env.example`：全部配置项的占位示例，不含真实凭据。
- `scripts/powercontext_setup.py`：创建稳定 Scope，可选执行一次记忆读写冒烟测试。
- `scripts/verify_dify.py`：通过 Dify Workflow API 连跑两轮并输出脱敏证据。
- `scripts/validate_submission.py`：检查 DSL 图结构、代码语法和常见密钥格式。
- `VERIFICATION.md`：评审样例、预期检查点和证据采集方法。

## 运行环境

- Dify Cloud 或 Dify self-hosted，支持 DSL `version: 0.3.0`。
- `langgenius/openai_api_compatible` 插件 `0.0.16`，或导入后将 LLM 节点重新绑定到可用的聊天模型。
- PowerContext 当前 `master`，Python 3.11+，`uv`。
- 任选一个 OpenAI Compatible 聊天模型。DSL 默认模型名是 `gpt-4o-mini`；模型名称不一致时，只需在“生成受记忆影响的新剧情”节点重新选择模型。
- 不需要 Embedding、Rerank、知识库、自定义插件、独立前端或额外数据库。

## 从零部署

### 1. 启动 PowerContext

按照 PowerContext 官方 API 快速开始安装并启动：

```bash
uv tool install --force "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@master"
powercontext server run
```

在另一个终端确认服务正常：

```bash
curl --fail http://127.0.0.1:8000/health/live
```

默认本地服务使用 SQLite。若启用了 Server 鉴权，在环境中设置 token；不要把 token 放进 URL、提示词、日志或记忆内容。

### 2. 创建可信 Scope

在仓库根目录执行：

```bash
python dify/scripts/powercontext_setup.py --seed-player setup-check-001
```

也可使用 `uv` 提供的 Python：

```bash
uv run --python 3.11 dify/scripts/powercontext_setup.py --seed-player setup-check-001
```

脚本输出 `scope_id`，并验证一次 `remember -> prepare` 闭环。把该 ID 留在可信配置中；不要接受用户或模型提供的 `scope_id`。

如 PowerContext 启用了鉴权，先在当前终端设置 `POWERCONTEXT_TOKEN`。脚本只读取它，不会打印它。

### 3. 导入并配置 Dify

1. 在 Dify 工作区选择“创建应用 -> 导入 DSL”，导入 `dify/time-echo-archive.yml`。
2. 安装 DSL 提示的 `OpenAI API Compatible` 插件，并配置可用模型服务。也可把 LLM 节点换成工作区已有模型。
3. 在工作流环境变量中设置：
   - `POWERCONTEXT_BASE_URL`：Dify 能访问的 PowerContext 地址，不要以 `/` 结尾。
   - `POWERCONTEXT_SCOPE_ID`：第 2 步脚本返回的固定 ID。
   - `POWERCONTEXT_TOKEN`：启用鉴权时填真实 token；本地未启用鉴权时可填任意非空占位值，服务会忽略该头。
4. 发布工作流，在 Dify 表单中直接体验，或在“访问 API”页面创建应用 API Key。

网络地址示例：

- Dify 在 Docker Desktop、PowerContext 在宿主机：通常使用 `http://host.docker.internal:8000`。
- 两者在同一个 Docker 网络：使用 PowerContext 的服务名和容器端口。
- Dify Cloud：PowerContext 必须使用可由 Dify Cloud 访问的 HTTPS 地址；不要暴露无鉴权的公网服务。

如果容器无法连接宿主机，请先确认 PowerContext 的监听地址和本机防火墙规则。生产环境应使用 HTTPS、Server 鉴权和最小网络暴露。

## 输入与输出

输入：

- `player_id`：跨轮稳定标识，最长 48 字符。例如 `echo-review-001`。
- `player_name`：剧情中的称呼。
- `choice`：本轮行动，可以是自由文本。

输出：

- `story`：本轮剧情、旧日回响、代价和下一轮 A/B 选择。
- `recall_status` / `recall_http_status`：PowerContext 召回证据。
- `memory_write_http_status` / `memory_write_receipt`：PowerContext 写入证据。
- `player_id`：本次实际使用的稳定标识。

## 安全与边界

- `POWERCONTEXT_SCOPE_ID` 只来自 Dify 环境变量，模型和玩家都不能选择其他 Scope。
- 召回内容在 system prompt 中明确标记为“不可信历史数据”，当前输入和系统规则优先。
- 用户文本由代码节点调用 `json.dumps` 序列化，输入中的引号、换行不会破坏请求体。
- 新 Scope 或无相关记忆时，`status: empty` 是正常结果；工作流会继续，但不伪造旧事件。
- HTTP 失败时不把错误响应当历史。网络完全不可达时，Dify HTTP 节点会按平台默认行为报错，便于发现配置问题。
- 当前实现以一个 Scope 承载比赛演示数据，并用 `player_id` 检索。多租户生产环境应为租户分配独立 Scope，并在 Gateway 做授权。

## 已知限制

- 最小 PowerContext Server 使用全文检索，因此 `player_id` 必须稳定且应具有区分度。
- LLM 输出是开放式剧情，没有唯一标准文本；验收应检查旧决定是否被明确引用、HTTP 状态和写入回执。
- 若模型提供商不识别 DSL 中的默认模型名，导入后重新选择一次模型即可，不影响其他节点。

## 参考资料

- PowerContext API 快速开始：https://powercontext.oceanbase.io/zh/docs/develop/api-quickstart/
- Dify HTTP Request 节点：https://docs.dify.ai/en/cloud/use-dify/nodes/http-request
- 赛题说明：https://oceanbase.yuque.com/org-wiki-obtech-vh7w9r/hc3pio/lhfslihtnnb8yqh6

本作品为本题原创工作流，没有复用第三方业务工作流或数据集。

## 提交前自检

```bash
uv run --python 3.11 --with pyyaml python dify/scripts/validate_submission.py
uv run --python 3.11 python -m py_compile dify/scripts/powercontext_setup.py dify/scripts/verify_dify.py
```

