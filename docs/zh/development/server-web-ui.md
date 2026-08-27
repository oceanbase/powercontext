# 添加 Server 托管页面

PowerContext 在提供 HTTP API 的同一个 FastAPI 进程中运行轻量的多页面 Web UI。Server 托管、通过 PowerContext
API 读取数据的页面应沿用这套结构。只有产品确实需要独立构建的前端应用时，才引入单独的 frontend build 或
client-side router。

## 目录约定

Web UI 按职责组织：

```text
src/powercontext/server/
├── web.py
├── static/
│   ├── auth.js
│   ├── dashboard.js
│   └── site.css
└── templates/
    ├── base.html
    ├── components/
    └── pages/
        └── dashboard.html
```

`web.py` 持有 Jinja environment、页面 router、static mount 和 UI 辅助 endpoint。`base.html` 持有 document head、
全局 header、footer 和资源插槽。`auth.js` 持有 bearer-token session storage 和 authenticated request。页面 template
只提供页面内容。`components/` 保存完整且可复用的片段，例如 login form、activity heatmap 和 recall trend。

template 和 static file 都是 package resource。它们必须放在 `powercontext.server` 下，确保 editable install 和构建后的
wheel 暴露相同文件。

## 添加页面

在 `templates/pages/` 下创建 template，并继承公共 layout：

```html
{% extends "base.html" %}

{% block title %}Page title{% endblock %}

{% block content %}
<section>
  <h1>Page heading</h1>
</section>
{% endblock %}
```

在 `mount_web_ui()` 中注册明确的 FastAPI route。将当前 `Request` 传给 `TemplateResponse`，使 Jinja 可以生成正确的
application URL：

```python
async def page(request: Request) -> Response:
    return _templates().TemplateResponse(
        request=request,
        name="pages/page.html",
        headers=_PAGE_HEADERS,
    )


router.add_api_route(
    "/page",
    page,
    methods=["GET"],
    response_class=HTMLResponse,
    name="page",
)
```

根路径是 Dashboard 入口。后续页面使用明确的独立 path，API 继续使用已有 versioned prefix。

## 理解 Dashboard 数据来源

浏览器先通过 `/dashboard/scopes` 完成认证并读取可选 scope，再使用所选 `scope_id` 和 `30d` period 请求
`/v1/stats`。Server 读取同一个 scope 的 snapshot，返回 inventory、model usage 和 recall statistics。

| Dashboard 数据 | 来源 |
| --- | --- |
| Sources | 当前 scope 的 Source journal position |
| Memory entries | 当前 Memory Artifact 中的 entry |
| Artifacts | 按 family 分组的当前 Artifact head |
| Pending review | 按 family 和 status 分组的当前 Candidate head |
| Model usage | 持久化的每日 generation 和 embedding usage |
| Recall 命中、Token 减少量与节约趋势 | 当前 estimator 对应的持久化每日 recall measurement |

Runtime 在一个 database transaction 中读取这些数据，并在 Server 端计算 total、pending Source、family count、daily
bucket 和 token reduction。浏览器将 `ready_preparations` 展示为 Recall 命中，并将每日有符号的
`token_reduction` 绘制为节约趋势。Heatmap 的每个日期格同时使用这两个字段，固定分档为：无命中、命中但没有正向
减少、减少 1–255、256–1023，以及 1024 个以上预估 Token。固定阈值避免稀疏活动和异常大值改变其他日期的颜色含义。

## 只复用稳定的页面结构

document-level 结构放在 `base.html`。一个片段已经被复用，或者本身是完整 UI 单元时，才放入
`templates/components/`。页面统一 import `auth.js`，不要各自实现 token storage 或 bearer header。页面专属的登录
错误、数据加载和渲染逻辑保留在该页面的 static script 中。

不要从单个 chart type 提前抽象通用 chart framework。先复用 markup 和 style；只有第二个页面需要相同行为后，
再提取 JavaScript data 或 rendering contract。

## 添加 Handoff Report 页面

启用 Handoff Report 后，Server 会在 `/handoff-reports` 托管 scope 交接页面，不再要求同时启用 scoped statistics Dashboard 或配置其 scope 列表。单独启用 Dashboard 时，根路径 `/` 仍提供原有统计页面。两个页面只复用 `base.html`、header、footer、`auth.js`、主题和 locale storage，不共享统计或报告计算逻辑。

Handoff Report 页面通过 `POST /v1/handoff-reports/scopes/list-known` 获取当前 token 可访问且存在 committed Handoff 的 exact `scope_id`，并将它作为可搜索范围选择器的值。选择范围后，浏览器通过 `POST /v1/handoff-reports/get` 发送必填 `scope_id` 请求 canonical JSON；Project catalog 和 `project_id` 不参与报告选择。页面继续以完整宽度显示当前精确交接快照。

当前快照把目标、当前状态、处置状态、下一步和已知缺失作为一份完整交接内容展示。一个“编辑”操作会同时打开五个字段，一个“保存新版本”操作会把完整内容 prepare 并 commit 为新的不可变 Handoff Revision。编辑器打开期间暂停 scope 切换和后台刷新。页面不提供接手方决策操作，已有连续性记录继续通过只读连续性时间线展示。除显式保存交接版本外，浏览器只格式化 `summary`、`coverage`、Workstream 状态和 digest，不重新计算报告口径。

已知 scope 请求成功但没有任何 committed Handoff 时，页面会以明确标记的无数据模板预览替代报告控件。点击重试会重新枚举 Handoff heads；一旦某个 scope 完成交接提交，就以真实报告替换预览。预览不会创建 Handoff，也不会请求伪造报告数据。

页面默认请求 UTC 当日周期，并提供本日、ISO 本周、自然月和自定义起止日期输入。日期输入的结束日按包含当天解释，发给 API 时转换为下一日零点的排他边界。当前 scope application 只规范化该输入，不提供 Activity event，`activity_coverage=not_configured`，也不返回 period comparison。Handoff 状态来自当前 exact selection，不能把它显示成历史期末状态。

概览请求可以关闭 evidence check 以降低延迟；Markdown 下载必须重新请求 `format=markdown`、`download=true` 且默认启用 evidence check。浏览器不得从已经渲染的 DOM 或 canonical JSON 自行拼接 Markdown。当前后台刷新和浏览器下载都要求已保存的 Bearer token，即使 Server 未启用鉴权也是如此；首次和手动报告加载仍可在无 token 时工作。Handoff Report 功能关闭时不注册 `/handoff-reports` 页面和对应 API，原 Dashboard 路径、scope 切换和统计请求保持不变。

## 保持安全边界

Dashboard shell 和 static asset 公开加载，以便浏览器显示登录表单。它们不得包含 bearer token、配置的 scope name、
statistics 或其他私有数据。UI 辅助 endpoint 和 `/v1/` data endpoint 继续由 `StaticBearerMiddleware` 保护。

Server 托管页面统一返回 Content Security Policy 和 `Cache-Control: no-store`。CSS 和 JavaScript 应使用外部文件。
`base.html` 中的短 inline script 只负责在首次绘制前应用已保存 theme。

## 验证行为

测试应通过公开 HTTP surface 验证页面 routing、受保护 data request、scope isolation，以及从真实 database-backed
Server 查询的数据。只断言用户可感知的 behavior，或保留一个具体 regression。不要断言 DOM ID、static asset path、
JavaScript source text、Jinja 内部实现或 private function call order。

运行：

```bash
uv run pytest tests/test_dashboard.py -q
make check
make test
make build
```

构建后确认 wheel 包含 `powercontext/server/templates/` 和 `powercontext/server/static/`。
