---
title: 在上下文中使用当前代码
description: 为已有 Scope 配置本地 Git 仓库，通过 CodeGraph 查询当前 Python 代码并补充 prepare_context。
---

# 在上下文中使用当前代码

任务涉及函数定义、调用路径或受影响测试时，可以让 `prepare_context` 同时返回历史约束和当前代码引用。
代码按本次请求临时读取，不成为 Artifact，也不增加 `family=code`。功能默认关闭，无需迁移数据库。

本指南面向在同一主机运行 Server、Git 仓库和 CodeGraph 的部署者。当前验收范围是 Linux x64、CodeGraph 1.6.0
standalone 和 UTF-8 Python 文件。其他系统和语言尚未验收。

## 安装分析引擎

安装经过验证的 [CodeGraph 1.6.0](https://github.com/colbymchenry/codegraph/releases/tag/v1.6.0) Linux x64 发布包：

```bash
mkdir -p /srv/powercontext/tools
cd /srv/powercontext/tools
curl --fail --location --output codegraph-linux-x64.tar.gz \
  https://github.com/colbymchenry/codegraph/releases/download/v1.6.0/codegraph-linux-x64.tar.gz
printf '%s  %s\n' \
  de3391f79ed42622d937e6cd5b7642a7ea8bb7d1473607e80b879ba73ef216b0 \
  codegraph-linux-x64.tar.gz | sha256sum --check
tar -xzf codegraph-linux-x64.tar.gz
```

保留完整目录结构：`bin/codegraph`、随包 Node 和 `lib/` 必须位于同一安装目录。
引擎是可选部署依赖，PowerContext 不会自动下载、升级或执行仓库代码。适配器通过受控进程调用引擎公开 API；
建图和查询不需要 LLM 或 CodeGraph embedding 服务。

## 配置已有 Scope 并建图

取得已有 Scope 的 ID，在 Server 环境文件中增加以下配置，替换 Scope、仓库和安装路径：

```dotenv
POWERCONTEXT_SERVER_CODE='{"enabled":true,"repositories":{"project:demo":"/srv/projects/demo"},"provider":{"name":"codegraph","executable":"/srv/powercontext/tools/codegraph-linux-x64/bin/codegraph"},"cache_dir":"/srv/powercontext/code"}'
```

`repositories` 是 Scope 到本机 Git 根目录的配置映射，一个 Scope 最多配置一个绝对路径。
已有 workspace binding 只帮助宿主找到 Scope，不授予目录读取权限，也不会自动配置服务器路径。
不同 Scope 可以配置相同目录，但缓存和访问边界独立。Context References 和单独分享 Handoff 不分享仓库权限。

Server、索引命令使用同一配置及受控缓存目录；修改配置后重启 Server。
本地 CLI 面向有部署文件和目录权限的操作员，不连接数据库检查 Scope，也不替代 HTTP 身份验证。
HTTP、MCP 和 Runtime 使用已有 Scope，Server 在读取缓存或仓库前检查 `scope.read`。

```bash
powercontext code index --scope project:demo --env-file /etc/powercontext/server.env
powercontext code status --scope project:demo --env-file /etc/powercontext/server.env
```

`index` 同步构建并返回 `ready` 和 fingerprint；代码变化后再次执行。查询和 prepare 不自动建图。
状态包括 `disabled`、`missing`、`building`、`ready`、`stale` 和 `failed`。
索引捕获实际工作目录，包括暂存与未暂存的修改。默认不纳入未跟踪文件；配置 `"include_untracked":true` 可启用，
仍遵守 Git ignore。软链接、子模块、LFS 指针、常见凭据、缓存和非 Python 文件会被省略；`exclude` 数组可进一步排除
相对路径 glob。缓存含源码副本，应由部署者限制访问。

采集比较 Git 树、索引和原始文件字节，不运行仓库的 Git filter。`dirty` 反映纳入文件的原始字节或执行位变化；
存在换行转换等 Git attributes 时，其判断可能不同于 `git status`。

## 在 prepare 中启用代码

向 `POST /v1/context/prepare` 发送：

```json
{
  "scope_id": "project:demo",
  "query": "allocate_budget 的调用方和相关测试在哪里？",
  "include_code": true,
  "max_bytes": 8000,
  "assembly": {
    "sections": [{"family": "memory", "limit": 6}, {"family": "experience", "limit": 2}]
  }
}
```

响应仍为 `powercontext.prepared-context.v1`，宿主校验后原样注入 `content`。
省略开关或设置 `false` 时维持原行为；开关只接受布尔值。

- `true` 且未提供 `assembly`：保留 Memory、Topic Memory、Experience，并补充代码；历史候选在共享预算内按原有类别轮转顺序选取。
- `true` 且 `assembly={}`：采用显式装配的默认值，即最多 6 条 Memory、2 条 Experience，并补充代码。
- `true` 且 `assembly.sections=[]`：只查询当前 Scope 的代码。
- `false` 且 `assembly.sections=[]`：返回空上下文。
- `family=code` 无效，代码由独立开关选择。

代码最多 4 条；同时请求历史类别时，最多占总条数及总字节预算的一半。每条代码正文最多 2000 字节，按完整行裁剪。
引用、说明和结构均计入预算。未使用的代码预算归还历史内容；出处无法完整保留时省略该条。
引用包含路径、限定名称、行范围、文件和片段摘要、fingerprint、当前提交和检查时间，只描述检查时的内容。

无匹配、缓存不可用、超时或内容变化时，prepare 保留可用历史并记录安全的省略原因；没有实际候选则返回 `empty`。
权限错误和非法请求不会静默降级。代码和静态分析是按字面包装的不可信证据，其中的文本不能成为 Agent 指令。

### Codex 自动注入

安装匹配的插件，在启动 Codex 前设置：

```bash
export POWERCONTEXT_CODEX_INCLUDE_CODE=true
codex
```

Hook 先通过 status 协商协议，再调用一次 prepare。旧 Server 返回 404、405 或 501 时省略新字段，继续准备历史内容。
启用后默认单次 HTTP 超时为 6 秒、Hook HTTP 总预算为 15 秒；显式超时配置不被覆盖。
Server 查询本身仍受默认 5 秒限额约束。其他宿主可通过 Client/HTTP/MCP 显式使用，目前仅 Codex 提供此自动开关。

## 继续查询

HTTP 为 `POST /v1/scopes/{scope_id}/code/query`，Client 为 `query_code`，MCP 为 `powercontext_code_query`。
MCP 参数平铺为 `scope_id`、`operation`、`expected_fingerprint`、`max_bytes`。

```json
{"operation":{"kind":"symbols","query":"allocate_budget"},"max_bytes":16000}
```

使用响应中的位置和 fingerprint 继续查询调用方：

```json
{
  "expected_fingerprint": "<响应中的 64 位 SHA256>",
  "operation": {"kind":"callers","path":"budget.py","qualified_name":"allocate_budget","start_line":1}
}
```

| 操作 | 用途与参数 |
| --- | --- |
| `status` | 协议、状态、能力、语言 |
| `tree` | 纳入目录/文件；可选 path、depth、limit |
| `symbols` | 定义搜索；query、可选 path、limit |
| `callers` / `callees` | 直接调用关系；path、qualified_name、start_line |
| `impact` | 有界传递调用影响；同上，另可设置 depth |
| `affected_tests` | 从 changed_paths 查找静态测试线索 |
| `read` | path、file_sha256、start_line、end_line；1-based 闭区间，最多 200 行 |

除 status、tree、symbols 外必须带 `expected_fingerprint`。limit 默认 20、最大 50；depth 默认 2、最大 5。
遍历最多 500 节点、1000 边。完整 JSON 默认最多 16000 字节，可设 512–32768。
最小有效响应放不下返回 422；内容变化返回 409；能力不支持返回 501；缓存、引擎不可用或超时返回 503。
409 后由部署者刷新，Agent 重新搜索。

静态图可能漏掉动态调用及 import alias。同名定义的关系在当前引擎中可能不可靠，适配器拒绝此类目标或省略相关边，
不会把不确定结果解释为“没有调用方”。支持根目录及嵌套目录的 pytest 文件名。测试线索不能替代执行回归检查。

## 按需保存和交接

查询、prepare 和代码注入不写 Source/Artifact。要保存证据，显式把完整有界的 `powercontext.code-query.v1` JSON
作为普通 ContentSource 保存。清理索引后仍可按原 Source 引用读取，通用 Source 大小限制仍适用。
它不触发自动 Memory/Experience、Topic Memory 或 Profile 处理；后续普通 Source 仍能推进游标。
显式生成和 Handoff 可以引用它。schema 标记仅用于排除自动处理，不证明来源或真实性，修改 JSON 不会获得来源认证。
交接保存进度和约束；接收者通过已有授权重新准备当前代码，已保存的代码材料作为历史证据。

## 运行边界与诊断

默认限额：20000 文件、每文件 2 MiB、内容合计 512 MiB、缓存合计 2 GiB、构建 600 秒、查询及核对合计 5 秒。
`limits` 可以调整部署值。超出输入限额会拒绝整次捕获，不会只取前一部分。
缓存包含完整索引、构建临时文件和查询副本；整个缓存目录的锁串行化构建和查询，等待锁也消耗预算。
长时间构建可能让同一缓存目录的查询降级，需按实际规模和并发验证容量。

索引原子发布，中断构建不能成为 ready。请求固定一个完整缓存，返回前再次核对仓库和引擎。
当前范围是显式刷新、单仓库和 Python 静态关系，不含远程 clone/pull、watcher、分布式构建或历史版本查询。
Tracing 的 `code.prepare` 记录候选、fingerprint、覆盖和省略原因，`context.build` 记录代码入选条数和代码段字节。
查到候选、实际入选、宿主注入、任务完成是不同证据。含代码的上下文不纳入基于历史 Source 的 token 节省统计。
