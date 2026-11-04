# 接通 MiniMax API：具体到每一步

## 0. 先澄清：`sk-...` 只是占位写法

真正的 key 是一整串字符，通常长得像：

```
sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

或者一串以 `eyJ` 开头的长字符串。**不要**把 `sk-...` 这几个字符当成 key 去填。

## 1. 在控制台复制 key

1. 浏览器打开 https://platform.minimaxi.com/ 并登录（用领了 Token Plan 的那个账号）；
2. 左侧菜单找到 **「接口密钥」/「API Keys」**（部分版本在「账户管理 → 接口密钥」下）；
3. 点 **「创建 API Key」**，名字随便起（例如 `seekdb-sdd`）；
4. 生成后**立刻整串复制** —— 多数平台只完整显示一次，关掉就看不到了。

## 2. 把它放到本机（两种方式，选一种）

### 方式 A：写进文件（推荐，不进 shell 历史、也不进任何对话记录）

```powershell
# 把 sk-xxxx 换成你复制到的真实 key
"sk-在这里粘贴你的完整key" | Set-Content -NoNewline -Encoding ascii "$env:USERPROFILE\.minimax_key"
```

验证读到了（只看长度和前缀，不回显整串）：

```powershell
cd D:\code\oceanbase\powercontext\minimax
python -c "from context import minimax; k=minimax.api_key(); print('len=', len(k), 'prefix=', k[:3])"
# 期望：len= 一个明显大于 20 的数字，prefix= sk-
```

### 方式 B：只放进当前 PowerShell 会话

```powershell
$env:MINIMAX_API_KEY="sk-在这里粘贴你的完整key"
$env:MINIMAX_MODEL="MiniMax-M2"     # 换成你套餐里实际可用的模型名
```

## 3. 自检（只发一个 “ping”，消耗极小）

```powershell
cd D:\code\oceanbase\powercontext\minimax
python -c "from context import minimax; print(minimax.run_api('ping').to_record())"
```

- 成功：`{'backend': 'api', ..., 'ok': True, ...}`
- key 不对：`'error': 'HTTP 401: {... authorized_error ... (1004)}'` → 回第 1 步重新复制完整 key
- 模型名不对：通常返回 4xx 且 message 里带 model/not found → 改 `MINIMAX_MODEL`
- 代理干扰：本机 `api.minimaxi.com` 可直连，若你开了全局代理导致异常，可临时
  `$env:NO_PROXY="api.minimaxi.com"`

## 4. 正式调用

```powershell
$env:SEEKDB_REPO="D:\code\oceanbase\seekdb"
python run.py agent --step prepare
```

产物：
- `agent/artifacts/after/prompt.md` —— 实际发给模型的完整提示词
- `agent/artifacts/after/answer.md` —— 模型原始回答
- `agent/artifacts/after/proposal.patch` —— 抽取出的补丁（校验通过才会有）
- seekdb `session_log` 里一条 `role='minimax'` 记录（后端、模型、提示词/回答摘要）

如果提示 `no patch proposal in this stage's answer`，说明回答里没有 `diff --git` 块，
可以：把 `answer.md` 里的 diff 手动整理成编辑器可用的补丁，或把
`MINIMAX_MAX_TOKENS` 调大后重跑。

## 5. 应用补丁（新进程，验证状态复用）

```powershell
python run.py state history
python run.py agent --step apply --worktree D:\code\seekdb
git -C D:\code\seekdb diff --stat
```

## 6. 更新记录并提交

把下面两处的“人工粘贴通路”改成实际结果（后端 `api`、模型名、调用时间、回答摘要、
补丁文件与行数）：

- `minimax/agent/MINIMAX-LOG.md`
- `minimax/docs/技术使用记录.md`（第 1 节表格 + 第 4 节版本表）

然后：

```powershell
cd D:\code\oceanbase\powercontext
git add minimax
git commit -m "docs(minimax): record the real MiniMax API run"
git push mine 2026-competition-ecosystem
git push mine minimax/sdd-context
```

## 常见问题

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `HTTP 401 ... (1004)` | key 没带全 / 过期 / 被撤销 | 重新复制整串；确认没有多余空格或引号 |
| `HTTP 404` | base URL 写错 | 保持默认 `MINIMAX_BASE_URL=https://api.minimaxi.com/v1`（本机已实测） |
| `no API key` | 环境变量与文件都没有 | 两种方式任选其一，见第 2 步 |
| 回答里没有 diff | 模型只给了说明 | 调大 `MINIMAX_MAX_TOKENS`，或在提示词后补一句“只输出 unified diff” |
| 想回退到粘贴方式 | — | 清空 `MINIMAX_API_KEY`、删除 `~/.minimax_key`，用 `MINIMAX_AGENT_OUTPUT=<文件>` |
