# 验证样例与证据

## 验证目标

用同一个 `player_id` 连续执行两轮，确认：

1. Dify 完成输入、HTTP、代码、LLM 和输出的完整编排。
2. 第一轮成功写入 PowerContext。
3. 第二轮 `recall_status` 为 `ready`，且新剧情明确引用第一轮的怀表、守钟人或表盘坐标，并产生后果。
4. 两轮都返回 PowerContext 写入回执，证明它不是只安装或只测试连接。

## 初始状态

- 使用新建 Scope，或使用此前从未出现过的 `player_id`：`echo-review-001`。
- PowerContext `/health/live` 返回成功。
- Dify 环境变量和模型已经按 `README.md` 配置，工作流已发布。

无需预置剧情数据。若要恢复初始状态，换一个全新的 `player_id` 即可；如需完全隔离，则用新的 `--idempotency-key` 创建新 Scope 并更新 Dify 环境变量。

## 手工验证

第一轮输入：

```text
player_id: echo-review-001
player_name: 评审员
choice: 我把会说话的怀表交给了守钟人，但偷偷记下了表盘上的坐标。
```

第一轮检查点：

- `recall_http_status` 为 `200`。
- 新玩家通常得到 `recall_status: empty`；如运行过初始化记忆，也可能是 `ready`。
- `story` 的“旧日回响”应为“尚未形成”或只引用确实召回到的初始化记忆。
- `memory_write_http_status` 为 `200` 或其他 2xx。
- `memory_write_receipt` 包含 PowerContext 返回的 `records`。

第二轮输入（保持同一个 `player_id`）：

```text
player_id: echo-review-001
player_name: 评审员
choice: 我沿着怀表坐标潜入午夜档案室，要求守钟人兑现承诺。
```

第二轮检查点：

- `recall_http_status` 为 `200` 且 `recall_status: ready`。
- `story` 的“旧日回响”明确提到第一轮交出怀表、守钟人或记录坐标的行为。
- 本轮场景因第一轮决定发生变化，而不是生成一段无关故事。
- `memory_write_http_status` 仍为 2xx，表明第二轮结果也已成为后续记忆。

## API 一键验证

在 Dify 的“访问 API”页面创建应用 API Key，只把它放入本地环境变量：

```bash
export DIFY_BASE_URL=http://localhost
export DIFY_API_KEY=app-your-local-secret
python dify/scripts/verify_dify.py
```

PowerShell：

```powershell
$env:DIFY_BASE_URL = 'http://localhost'
$env:DIFY_API_KEY = 'app-your-local-secret'
uv run --python 3.11 dify/scripts/verify_dify.py
```

脚本按上述顺序调用 `POST /v1/workflows/run` 两次，只输出故事、HTTP 状态和“是否存在写入回执”；不会打印 API Key 或 PowerContext token。

## 节点级证据

在 Dify “日志与标注 -> 工作流日志”打开第二轮运行，依次检查：

- `PowerContext 召回`：请求 URL 以 `/v1/context/prepare` 结尾，响应为 2xx。
- `解析历史回声`：`recall_status` 为 `ready`，`history` 中包含第一轮文本。
- `生成受记忆影响的新剧情`：输入历史被包在 `<historical_context>` 中，输出引用旧选择。
- `PowerContext 写入`：请求 URL 以 `/v1/memory/remember` 结尾，响应包含 `records`。

截图或录屏时应遮盖 Dify API Key、PowerContext token 和完整 Authorization 头。DSL 与本目录文档均只包含占位值。

## 运行证据记录模板

执行验证后，把脚本的脱敏 JSON 输出保存为评审记录，至少应呈现以下状态变化：

```json
[
  {
    "round": 1,
    "recall_http_status": 200,
    "recall_status": "empty",
    "memory_write_http_status": 200,
    "memory_write_receipt_present": true
  },
  {
    "round": 2,
    "recall_http_status": 200,
    "recall_status": "ready",
    "memory_write_http_status": 200,
    "memory_write_receipt_present": true
  }
]
```

上面是判定格式，不是伪造的实际输出；实际故事文本会因所绑定模型而变化。评审可通过工作流输出和节点日志现场复现。

