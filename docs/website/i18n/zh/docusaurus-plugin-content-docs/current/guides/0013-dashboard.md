---
title: PowerMem Web Dashboard 使用指南
sidebar_label: Web Dashboard 使用指南
---

# PowerMem Web Dashboard 使用指南 {#powermem-web-dashboard-guide}

PowerMem Web Dashboard 提供了一个可视化界面，用于检查、管理和监控您的 AI Agent 记忆。它随 HTTP API 服务器一起提供，支持实时分析、记忆的 CRUD 操作、用户配置文件检查以及系统健康监控。

---

## 目录 {#table-of-contents}

- [快速开始](#getting-started)
  - [启动服务器](#starting-the-server)
  - [访问 Dashboard](#accessing-the-dashboard)
  - [自动打开浏览器](#automatic-browser-opening)
  - [Docker 部署](#docker-deployment)
- [身份验证](#authentication)
  - [配置 API 密钥](#configuring-api-keys)
  - [在 Dashboard 中设置 API 密钥](#setting-api-key-in-dashboard)
- [Dashboard 页面](#dashboard-pages)
  - [概览页面](#overview-page)
  - [记忆页面](#memories-page)
  - [用户配置文件页面](#user-profile-page)
  - [设置页面](#settings-page)
- [常见工作流程](#common-workflows)
  - [在 SDK 集成后检查记忆](#inspect-memories-after-sdk-integration)
  - [调试检索质量](#debug-retrieval-quality)
  - [监控系统健康](#monitor-system-health)
- [故障排除](#troubleshooting)
  - [Dashboard 显示 404](#dashboard-shows-404)
  - [401 未授权错误](#401-unauthorized-errors)
  - [数据陈旧或丢失](#stale-or-missing-data)
  - [重建 Dashboard 资源](#rebuilding-dashboard-assets)
- [与其他界面的关系](#relationship-to-other-interfaces)

---

## 快速开始 {#getting-started}

### 启动服务器 {#starting-the-server}

Web Dashboard 由 PowerMem HTTP API 服务器提供服务。要启动服务器：

> **注意：** 如果您已经运行了 PowerMem 服务器，可以跳过此步骤，直接前往 [访问 Dashboard](#accessing-the-dashboard)。
```bash
# 使用 pip 安装的服务器
powermem-server --host 0.0.0.0 --port 8848

# 或使用 Makefile（开发环境）
make server-dashboard-start
```
服务器会从当前目录下的 `.env` 文件中读取配置。有关初始设置，请参阅[入门指南](./0001-getting_started.md)。

### 访问仪表板 {#accessing-the-dashboard}

当服务器运行后，打开浏览器并导航至：
```
http://localhost:8848/dashboard/
```
> **注意：** 末尾的斜杠非常重要。访问 `/dashboard` 时如果没有斜杠，会自动重定向。

您还可以通过以下地址访问 API 文档（Swagger UI）：
```
http://localhost:8848/docs
```
### 自动打开浏览器 {#automatic-browser-opening}

当你从交互式本地终端启动 `powermem-server` 且已构建的 Dashboard 资源可用时，PowerMem 会等待 `/dashboard/` 响应，然后在你的默认浏览器中打开它。绑定地址 `0.0.0.0` 和 `::` 会被转换为回环 URL（例如，`http://localhost:8848/dashboard/`）。
```bash
# 禁用自动打开浏览器
powermem-server --no-open-browser

# stdout 被重定向时显式打开（例如 Makefile 后台启动）
powermem-server --open-browser
```
即使使用了 `--open-browser`，在 CI、容器、SSH 会话、无头环境以及 Dashboard 资源不可用时，浏览器也不会自动打开。

`make server-dashboard-start` 目标会传递 `--open-browser`，因此在后台服务器启动后，Dashboard 会自动打开。

### Docker 部署 {#docker-deployment}

如果您使用 Docker Compose：
```bash
docker-compose -f docker/docker-compose.yml up -d
```
仪表板可以通过相同的 URL 访问：`http://localhost:8848/dashboard/`

Docker 和其他无头部署不会尝试自动启动浏览器。请从客户端机器打开仪表板 URL。

有关详细的部署说明，请参阅 [Docker & Deployment Guide](https://github.com/oceanbase/powermem/blob/main/docker/README.md)。

---

## 认证 {#authentication}

### 配置 API 密钥 {#configuring-api-keys}

默认情况下，PowerMem 服务器运行时认证功能是**禁用**的。要启用认证功能：

1. 打开您的 `.env` 文件
2. 设置以下变量：
```bash
# 启用认证
POWERMEM_SERVER_AUTH_ENABLED=true

# 设置一个或多个 API key（用逗号分隔）
POWERMEM_SERVER_API_KEYS=your-secret-key-1,your-secret-key-2

# 或使用单个 key（旧格式）
POWERMEM_SERVER_API_KEY=your-secret-key
```
3. 重启服务器（如果服务器已经在运行；否则，只需启动它）：
```bash
powermem-server --host 0.0.0.0 --port 8848
```
> **安全提示：** 当启用身份验证时，所有 API 端点都需要通过 `X-API-Key` 头或 `?api_key=` 查询参数传递有效的 API 密钥。

### 在 Dashboard 中设置 API 密钥 {#setting-api-key-in-dashboard}

如果启用了身份验证，您需要在 Dashboard 中配置 API 密钥：

1. 进入 **Settings** 页面（点击侧边栏中的齿轮图标）
2. 在 "API Key" 字段中输入您的 API 密钥
3. 点击 **Save**

API 密钥存储在您浏览器的 `localStorage` 中，键为 `powermem_api_key`。它不会离开您的浏览器，仅在向 PowerMem 服务器发送 API 请求时使用。

> **隐私提示：** API 密钥存储在您的浏览器本地。清除浏览器数据会删除密钥，您需要重新输入。

---

## Dashboard 页面 {#dashboard-pages}

Dashboard 包含四个主要页面，可通过左侧边栏导航访问：

### 概览页面 {#overview-page}

**路径：** `/dashboard/`

概览页面提供您的记忆系统的实时分析和系统健康监控。

#### 功能 {#features}

**统计卡片：**
- **Total Memories** — 存储的记忆记录总数
- **Avg. Importance** — 所有记忆的平均优先级分数（0.00–5.00 范围）
- **Access Density** — 每条记忆记录的平均访问次数
- **Unique Dates** — 有记忆活动的不同日期数量

**图表和可视化：**

1. **Growth Trend** — 折线图显示每日记忆创建量随时间的变化
2. **Memory Categories** — 环形图显示按分类类型划分的记忆分布
3. **Hot Memories** — 显示访问次数最多的记忆表格，包含内容片段和访问次数
4. **Retention Age** — 柱状图显示记忆生命周期分布（< 1 天、1-7 天、7-30 天、> 30 天）
5. **记忆质量** — 显示质量分析的卡片（见下文）

**记忆质量卡片：**

显示记忆健康指标，包括：
- **Low Quality Ratio** — 存在质量问题的记忆比例
- **Quality Issues Breakdown** — 水平柱状图显示以下问题的数量：
  - 缺少元数据
  - 内容为空
  - 无 Embedding
  - 低重要性

质量状态颜色编码：
- ≤ 10%: 良好（绿色）
- ≤ 20%: 良好（蓝色）
- ≤ 50%: 警告（黄色）
- > 50%: 严重（红色）

**System Health 卡片：**

显示实时系统状态，每 30 秒自动刷新：
- **Overall Status** — 正常运行 / 降级 / 停机
- **Uptime** — 人性化显示的持续时间（例如 "2d 5h 32m"）
- **Configuration** — 存储类型和 LLM 提供商
- **Dependencies** — 服务健康状态表格，包含延迟指标

**过滤器：**

- **Time Range Selector** — 下拉菜单按时间范围过滤数据：最近 7 天、30 天、90 天或全部时间
- **User ID Filter** — 在 URL 中传递 `?user_id=xxx` 以将所有数据限定到特定用户
- **Agent ID Filter** — 在 URL 中传递 `?agent_id=xxx` 以将所有数据限定到特定 Agent
- **刷新按钮** — 手动刷新所有数据
- **Clear Filters** — 当过滤器激活时出现，返回未过滤视图

### 记忆页面 {#memories-page}

**路径：** `/dashboard/memories`

记忆页面允许您浏览、搜索、过滤和删除存储的记忆记录。

#### 功能 {#features-1}

**过滤面板：**
- **User ID** — 按用户标识符过滤记忆
- **Agent ID** — 按 Agent 标识符过滤记忆
- **Content Search** — 在记忆内容中进行子字符串搜索（客户端侧）
- **Apply Filters** — 应用当前过滤条件
- **Clear All** — 重置所有过滤器

**记忆表格：**

显示记忆，包含以下列：
- **User ID** — 截断显示，鼠标悬停显示完整值
- **Agent ID** — 截断显示，鼠标悬停显示完整值
- **Content** — 前 120 个字符，鼠标悬停显示完整文本
- **Metadata** — 显示前 2 个键值对作为徽章
- **Created At** — 创建日期（小屏幕上隐藏）

**分页：**

- 每页 20 条记录
- 上一页/下一页导航按钮
- 页码保存在 URL 中以便分享视图

**行操作（点击任意行的 `⋯` 菜单）：**

1. **View Details** — 打开详细信息面板，显示：
   - Memory ID
   - 完整内容（保留空白）
   - 分类徽章
   - 创建时间（完整日期时间）
   - User ID 和 Agent ID
   - Run ID（来自 `run_id` 字段或 `metadata.filters.run_id`）
   - 格式化的完整元数据 JSON

2. **删除记忆** — 永久删除记忆记录（需要确认）

3. **Copy Raw JSON** — 将完整的记忆 JSON 复制到剪贴板

> **注意：** Dashboard 当前仅支持 **读取** 和 **删除** 操作。要创建或更新记忆，请使用 Python SDK、CLI (`pmem`) 或 REST API。

### 用户档案页面 {#user-profile-page}

**路径：** `/dashboard/user-profile`

用户档案页面显示由 PowerMem 智能记忆系统创建的聚合用户级记忆档案。

#### 功能 {#features-2}

**搜索：**
- **User ID Input** — 输入用户 ID 以搜索特定档案
- **Fuzzy Search** — 当提供搜索词时，API 支持模糊匹配
- 按 **Enter** 或点击搜索按钮执行搜索

**档案表格：**
显示用户档案信息，包括：
- **User ID** — 截断显示，鼠标悬停显示完整值
- **Profile Content** — 在宽度为 420px 时截断，鼠标悬停显示完整内容
- **Topics** — 显示前三个主题键，若有更多则显示 "..." 指示符
- **Updated At** — 最近更新时间戳（完整日期时间）

**分页：**

- 每页显示 20 个档案
- 上一页/下一页导航

**查看详情：**

点击任意行的 **View Details** 按钮，打开详情面板，显示以下内容：
- User ID
- 完整档案内容
- 以格式化 JSON 显示的 Topics
- Created At 和 Updated At 时间戳

> **注意：** 用户档案检查是 **只读** 的。档案由 PowerMem 的记忆管道根据用户交互自动生成和更新。

### 设置页面 {#settings-page}

**路径：** `/dashboard/settings`

设置页面用于管理仪表板的身份验证配置。

#### 功能 {#features-3}

**API Key 管理：**
- **API Key 输入框** — 密码掩码字段，用于输入 PowerMem 服务器的 API key
- **保存按钮** — 将密钥存储在浏览器的 localStorage 中
- **提示文字** — "如果服务器上的 `auth_enabled` 设置为 true，则此项为必填。"

**隐私声明：**

一个蓝色信息横幅解释 API key 仅存储在您的浏览器 localStorage 中，且不会传输到 PowerMem 实例以外的任何服务器。

---

## 常见工作流程 {#common-workflows}

### 在 SDK 集成后检查记忆 {#inspect-memories-after-sdk-integration}

在使用 Python SDK 或 LangChain 将 PowerMem 集成到您的应用程序后：

1. 运行您的应用程序并执行一些创建记忆的操作
2. 打开仪表板 `http://localhost:8848/dashboard/`
3. 导航到 **Overview** 页面查看：
   - 总记忆数量的增长
   - 增长趋势图的更新
   - 记忆类别分布
4. 导航到 **Memories** 页面以：
   - 浏览所有存储的记忆
   - 按 `user_id` 或 `agent_id` 过滤以检查特定用户/Agent 数据
   - 点击 **View Details** 检查单个记忆内容和元数据
   - 验证智能提取和分类是否正常工作

### 调试检索质量 {#debug-retrieval-quality}

如果您的 AI Agent 未检索到预期的记忆：

1. 转到 **Memories** 页面
2. 使用 **Content Search** 过滤器查找与查询相关的记忆
3. 检查 **记忆质量** 卡片（在概览页面）：
   - 高 "Low Quality Ratio"（表示许多记忆存在问题）
   - 缺少 embeddings（没有 embeddings 的记忆无法进行向量搜索）
   - 内容为空或缺少元数据
4. 对于有问题的记忆：
   - 点击 **View Details** 检查完整的 JSON 结构
   - 验证 `embedding` 字段是否存在
   - 检查 `metadata` 是否包含预期字段
5. 如果质量较差，请考虑：
   - 使用更好的提示重新运行记忆提取
   - 删除低质量记忆并重新添加
   - 检查 `.env` 中的 embedding 提供商配置

### 监控系统健康状况 {#monitor-system-health}

确保您的 PowerMem 服务器运行正常：

1. 打开 **Overview** 页面
2. 检查 **System Health Card**（每 30 秒自动刷新）：
   - **Overall Status** 应显示 "Operational"（绿色）
   - **Uptime** 显示服务器运行时间
   - **Dependencies** 表显示 LLM、embedding 和存储服务的健康状况
3. 如果任何依赖项显示为 "Degraded" 或 "Down"：
   - 检查依赖项表中的错误信息
   - 验证 `.env` 中该提供商的配置
   - 检查与外部服务（如 OpenAI、Qwen 等）的网络连接
4. 持续监控 **记忆质量**：
   - 上升的 "Low Quality Ratio" 可能表明记忆提取存在问题
   - 定期监控有助于及早发现退化

---

## 疑难解答 {#troubleshooting}

### 仪表板显示 404 {#dashboard-shows-404}

**症状：** 访问 `http://localhost:8848/dashboard/` 返回 404 错误。

**原因：** 仪表板资源尚未构建或未放置在正确位置。

**解决方案：**
```bash
# 构建 Dashboard 资源
make build-dashboard

# 验证资源是否存在
ls src/server/dashboard/index.html

# 重启服务器（如果服务器尚未运行，则直接启动即可）
make server-stop
make server-start
```
> **注意：** 服务器会在 `src/server/dashboard/` 中查找构建后的资源文件。如果该目录不存在，`/dashboard/` 路由将不会被挂载（不会报错，但会返回 404）。

### 401 未授权错误 {#401-unauthorized-errors}

**症状：** Dashboard 显示“401 Unauthorized”或 API 请求返回 401。

**原因：** 服务器启用了身份验证，但 Dashboard 中未配置 API 密钥。

**解决方案：**

1. 打开 Dashboard 的 **Settings** 页面
2. 输入您的 API 密钥（来自 `.env` 文件：`POWERMEM_SERVER_API_KEY` 或 `POWERMEM_SERVER_API_KEYS`）
3. 点击 **Save**
4. 刷新页面

如果您没有 API 密钥：
1. 检查您的 `.env` 文件中是否有 `POWERMEM_SERVER_API_KEYS` 或 `POWERMEM_SERVER_API_KEY`
2. 如果不需要身份验证，请设置 `POWERMEM_SERVER_AUTH_ENABLED=false` 并重启服务器（或启动服务器，如果尚未运行）

### 数据过时或缺失 {#stale-or-missing-data}

**症状：** Dashboard 未显示最近的记忆或显示过时的信息。

**可能原因及解决方案：**

1. **浏览器缓存：** 点击概览或记忆页面上的 **刷新** 按钮
2. **错误的 user_id/agent_id 过滤器：** 检查 URL 中是否有 `?user_id=xxx` 或 `?agent_id=xxx` 参数。如果存在，点击 **Clear Filters**。
3. **服务器未运行：** 验证服务器是否正在运行：`curl http://localhost:8848/api/v1/system/health`
4. **不同的数据库：** 确保您的应用程序和服务器使用相同的 `.env` 配置（相同的数据库）

### 重新构建 Dashboard 资源文件 {#rebuilding-dashboard-assets}

如果您修改了 Dashboard 的源代码或遇到了 UI 问题：
```bash
# 清理并重新构建
cd dashboard
rm -rf dist node_modules
pnpm install
pnpm build
cd ..

# 复制到服务器目录
make build-dashboard

# 重启服务器（如果服务器尚未运行，则直接启动即可）
make server-stop
make server-start
```
> **开发提示：** 为了进行活跃的仪表盘开发，您可以单独运行 Vite 开发服务器（`cd dashboard && pnpm dev`），并将其配置为代理 API 请求到您的后端。详情请参阅[开发指南](../development/overview.md)。

---

## 与其他接口的关系 {#relationship-to-other-interfaces}

Web Dashboard 是与 PowerMem 交互的几种方式之一：

| 接口 | 目的 | 最适合的场景 |
|------|------|-------------|
| **Web Dashboard** | 可视化检查和监控 | 浏览记忆、分析、系统健康 |
| **Python SDK** | 编程式记忆操作 | 应用集成、自定义工作流 |
| **CLI (`pmem`)** | 命令行记忆操作 | 快速查询、脚本编写、备份/迁移 |
| **REST API** | 基于 HTTP 的编程访问 | 自定义集成、非 Python 应用 |
| **MCP Server** | AI 客户端集成 | Claude Code、Cursor、Copilot 等 |
| **VS Code Extension** | 集成到 IDE 的记忆访问 | 开发者工作流、快速笔记 |

### Web Dashboard 与 VS Code Extension Dashboard 的区别 {#web-dashboard-vs-vs-code-extension-dashboard}

VS Code 扩展还在状态栏中提供了一个“Dashboard”按钮。这与 Web Dashboard **不同**：

- **Web Dashboard** (`/dashboard/`): 功能全面的 Web 应用，包含分析、图表、记忆管理和系统健康监控
- **VS Code Extension Dashboard**: 在 VS Code 中的嵌入式浏览器面板中打开相同的 Web Dashboard，提供无需离开 IDE 的快速访问

这两种界面都连接到相同的 PowerMem 服务器，并显示相同的数据。

### 何时使用 Dashboard {#when-to-use-the-dashboard}

在以下情况下使用 Web Dashboard：
- 直观地探索和浏览记忆
- 监控系统健康和记忆质量指标
- 分析记忆增长趋势和使用模式
- 检查用户配置文件和记忆元数据
- 排查检索或质量问题

在以下情况下使用 SDK、CLI 或 API：
- 以编程方式创建或更新记忆
- 将记忆操作集成到您的应用中
- 执行批量操作或迁移
- 自动化记忆管理任务

---

## 其他资源 {#additional-resources}

- [API 服务器文档](../api/0005-api_server.md) — 服务器配置和部署
- [入门指南](./0001-getting_started.md) — 初始设置和 SDK 使用
- [配置指南](./0003-configuration.md) — 环境变量和设置
- [Docker 和部署](https://github.com/oceanbase/powermem/blob/main/docker/README.md) — 生产环境部署
- [开发指南](../development/overview.md) — 构建和自定义仪表盘
