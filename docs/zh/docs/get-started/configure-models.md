---
title: 配置模型与完整记忆
description: 理解 Generation、Embedding、后台调度及各制品的触发条件，验证模型驱动的记忆处理。
---

# 配置模型与完整记忆

[快速开始](quickstart.md)默认通过向导配置完整记忆。本页解释要准备哪些 API、哪些能力需要模型，
以及保存配置后仍要完成的策略和业务检查。

## 模型连接怎么选

运行 `powercontext config init --language zh --output .env`，选择完整记忆或自定义能力。
向导根据所选能力询问需要的连接；API 协议描述的是模型服务的接口，不是你正在使用哪个 Coding Agent。

| 连接 | 用途 | 需要准备 |
| --- | --- | --- |
| Generation | Memory 提取、Topic Memory、Profile、Experience 和 Skill 生成 | 服务支持的协议、完整 Base URL、API key、生成模型名 |
| Embedding | 语义检索与向量索引 | Embedding API 地址、凭据、模型名、模型实际支持的维度 |
| Rerank（可选） | 对检索候选进一步排序 | 按向导选择复用生成连接或单独配置重排连接 |

后台能力默认共用配置的 Generation Provider，不会自动复用 Codex/Claude 的订阅会话或登录凭据。
基础记忆不要求独立 Generation API：Agent 可以通过 MCP 显式保存自己整理的内容，但 Server 不会因此自动处理普通 Source。

“OpenAI-compatible Chat Completions”“OpenAI Responses”“Anthropic-compatible Messages”是不同协议；
按模型服务商实际提供的接口选择。OpenAI-compatible 也可以指第三方服务，不表示必须购买某个 Agent 订阅。
向导不会验证 API key、额度、模型可用性或远程连通性；启动后的 `ready` 和真实处理才会检验这些条件。

例如使用 OpenAI provider 时，模型标识由 provider 前缀和模型名组成：

```dotenv
OPENAI_API_KEY=<从受保护的环境或 secret manager 提供>
POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL=openai:gpt-4.1-mini
```

实际可用模型仍取决于 provider 账户和区域；其他 provider 也使用 `provider:model-name` 形式，请使用该 provider
支持的模型名，不要直接复制上面的 provider 配置。

Embedding 连接可以与 Generation 共用地址和凭据，但通常使用不同模型。
向量维度必须是所选 Embedding 模型实际支持的输出维度。Embedding Profile ID 是标识这套模型、维度和向量约定的名称，
不是额外 API key。首次使用可接受向导建议；复用已有向量数据时，必须保持其对应关系，不能随意改 ID 或维度来消除报错。

## 完整能力不等于每条对话都会生成所有制品

| 能力 | 自动产生内容的条件 |
| --- | --- |
| Memory | 普通 Source 已采集、Generation 可用、Memory 处理调度启用 |
| Topic Memory | 有可归纳的 Source、Generation 满足 Topic 的配置要求、Topic 调度启用 |
| Profile | Generation 和 Profile 调度启用，且目标 Scope 的生成策略已启用 |
| Experience | 具有任务结果语义的 `task-outcome` Source；产生待审候选，不自动批准 |
| Skill | 按需触发生成；没有“聊一句就自动生成 Skill”的固定调度 |
| 语义检索 | Embedding 可用、模型与已有向量 Profile/维度一致 |

Memory、Topic Memory 和 Experience 各有独立检查周期；Profile 使用独立 cron。
向导给出的推荐值会写入本次配置，不是每种制品都固定每 60 秒完成一次。
Generation 单次请求超时、Worker 总超时和检查间隔分别控制不同事情，修改其中一项不会替代另外两项。

配置了完整能力之后，也不要把普通闲聊当作 Experience/Skill 的充分输入。
需要这些能力时，按[经验工作流](../workflows/create-and-review-experience.md)提供任务结果并完成相应审核。
Profile 审核行为见[Scope 画像](../workflows/use-profiles.md)。

## 启动后检查

在 Server 终端执行：

```bash
powercontext config validate --env-file .env
powercontext server run --env-file .env
```

另一个终端只加载客户端文件：

```bash
set -a
. ./.env
set +a
powercontext ready
powercontext capabilities
```

Readiness 的数据库/Runtime 状态与已配置模型依赖都需要检查。`degraded` 不代表模型提取正常；
只有 `auto, fts` 检索而没有 `vector, hybrid` 时，检查 Embedding 连接、Profile 和维度。
不要在 Agent 终端加载包含模型凭据的 Server `.env`；客户端文件已经包含所需连接设置。

## 为 Scope 启用 Profile 策略

先按[快速开始](quickstart.md#3-创建-scope-并安装-codex-插件)创建并绑定真实 Scope，重新加载客户端环境。
以下例子为 Codex 的 Scope 启用“生成后人工审核”；使用 Claude Code 时，将第一行变量换成
`POWERCONTEXT_CLAUDE_SCOPE_ID`。先读取现有策略版本；仅在策略不存在（404）时使用版本 0：

```bash
export POWERCONTEXT_TEST_SCOPE_ID="$POWERCONTEXT_CODEX_SCOPE_ID"
python3 - <<'PY'
import json
import os
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

base = os.environ["POWERCONTEXT_CLIENT_SERVER_URL"].rstrip("/")
scope = quote(os.environ["POWERCONTEXT_TEST_SCOPE_ID"], safe="")
url = f"{base}/v1/scopes/{scope}/profile-policy"
headers = {
    "Authorization": "Bearer " + os.environ["POWERCONTEXT_CLIENT_API_TOKEN"],
    "Content-Type": "application/json",
}
try:
    with urlopen(Request(url, headers=headers), timeout=30) as response:
        version = json.load(response)["version"]
except HTTPError as error:
    if error.code != 404:
        raise
    version = 0
payload = json.dumps({
    "generation_enabled": True,
    "activation_mode": "review_required",
    "expected_version": version,
}).encode()
with urlopen(Request(url, data=payload, headers=headers, method="PUT"), timeout=30) as response:
    print(json.dumps(json.load(response), indent=2))
PY
```

如果其他操作同时修改了策略，版本冲突时重新读取后再更新。不要把 `expected_version: 0` 作为每次更新的固定值。
启用后按配置的 cron 处理有新 Source 的 Scope。审核模式先产生 Candidate，批准前不会替换正式 Profile；
启用策略本身不会立即生成内容。若需立即验证已有输入，可按[Scope 画像](../workflows/use-profiles.md)调用 Profile flush。

## 验证真实提取与召回

主验收按[快速开始](quickstart.md#4-用普通对话验收-topic-memory)完成：

1. Agent 普通输入进入绑定 Scope 的 Source。
2. Topic Memory 出现对应主题和证据。
3. 第二条相关输入进入 Source 后，检查主题内容或修订是否反映更新。
4. 新会话使用同一 Scope 召回并返回 citation。

如果需要单独诊断 Memory 自动提取，可在真实 Source 已入库后，对同一 Scope 调用：

```bash
curl -fsS -X POST "$POWERCONTEXT_CLIENT_SERVER_URL/v1/memory/flush" \
  -H "Authorization: Bearer $POWERCONTEXT_CLIENT_API_TOKEN" \
  -H 'Content-Type: application/json' \
  -d "{\"scope_id\":\"$POWERCONTEXT_CODEX_SCOPE_ID\"}"
```

核对返回游标是否推进，并查看该 Scope 的 Memory 与 Source 引用。`idle` 可能表示后台已处理，
不代表没有记忆；Memory flush 也不等于 Topic/Profile/Experience 的全部处理完成。
查看用量可运行 `powercontext stats --scope-id "$POWERCONTEXT_CODEX_SCOPE_ID"`。

为了让验收记录可复现，在发送测试输入前先分配一个来源标识，再检查返回的条目。列表响应包含 `current_cursor`，
每个条目包含 `position`、`entry_id`、`source_refs` 和 `matched_by`；这些字段可以区分采集到的证据与之后生成的制品。

```bash
SOURCE_ID="quickstart-$(date +%s)-$$"
echo "请将测试输入标记为来源：$SOURCE_ID"
curl -fsS "$POWERCONTEXT_CLIENT_SERVER_URL/v1/memory/entries/list?scope_id=$POWERCONTEXT_CODEX_SCOPE_ID" \
  -H "Authorization: Bearer $POWERCONTEXT_CLIENT_API_TOKEN"
```

| 现象 | 优先检查 |
| --- | --- |
| Source 为空 | Hook 是否加载、采集是否启用、URL/Token/Scope 是否一致 |
| Source 有内容但主题不更新 | Topic 调度、模型错误、Worker 状态及内容是否值得更新 |
| 请求超时 | 模型连接与响应时间、单次请求及 Worker 超时，避免只改检查周期 |
| Dashboard 与 Agent 内容不同 | 两者的 Server 地址和真实 Scope ID |
| 新会话找不到内容 | Scope 绑定、上下文组装/显式搜索、保存记录及 citation |

更多检查见[故障排查](../operate/troubleshoot.md)与[完整配置参考](../operate/configuration.md)。
