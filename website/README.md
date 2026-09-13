# PowerContext website

PowerContext 官网使用 Next.js 和 Fumadocs 构建，包含双语产品页、文档、基准测试、更新日志、HTTP API 和 Python API 参考。

## 内容边界

- `docs/en` 与 `docs/zh` 保存双语源内容。
- 构建脚本把 `docs/<locale>/docs` 复制到被 Git 忽略的 `website/content/docs`，供 MDX 编译。
- 首页与基准测试页的双语文案仍从 `docs/<locale>` 读取。
- OpenAPI 契约和 Python 源码在构建时生成对应的 API 参考页。

## 本地运行

需要 Node.js 22、pnpm 11、uv。

```bash
cd website
pnpm install
pnpm dev
```

完整验证：

```bash
pnpm types:check
pnpm lint
pnpm test
pnpm build
```

静态产物输出到 `website/out`。

## GitHub Star 入口

双语页面共用导航中的 GitHub 图标和数量徽标，仓库地址由 `NEXT_PUBLIC_REPOSITORY_URL` 控制。
`Website Star snapshot` 工作流每 15 分钟查询 GitHub API，把数量及获取时间写入独立的
`website-stats` 分支中的 `github-stars.json`。该分支只保存数据，不修改源码、不构建或发布官网。
数量不变时跳过提交，至少每天更新一次时间戳；取数失败时任务报错，并保留已发布快照。

浏览器只异步读取该文件的 GitHub Raw CDN 地址，每个页面会话最多请求一次，各导航实例共用结果。
没有 GitHub API 调用、后台轮询或 Star 数量的浏览器存储；请求超时为 5 秒。
取数前预留数字徽标的空间，不显示 `Star` 占位文字；收到有效数量后再显示徽标。
文件尚未生成、断网或数据无效时，GitHub 图标仍可点击，不显示假数字。
这不是即时推送：新页面加载最近的 CDN 快照，已打开的页面需刷新才能获取新数量。
首次可见时显示一次轻量关注提示，也支持悬停、键盘焦点和 Escape 关闭。

定时工作流合并到 `master` 后生效；可手动运行一次 `Website Star snapshot` 初始化数据。
GitHub 调度及 CDN 缓存可能带来额外延迟。只有发布任务拥有 `contents: write` 权限，
使用内置 `GITHUB_TOKEN`，浏览器不需要凭据或服务端接口。PR 仅运行只读测试。
若更换 `NEXT_PUBLIC_REPOSITORY_URL`，目标仓库也需要提供同路径、同格式的快照。

## 发布到 GitHub Pages

在正式仓库 `oceanbase/powercontext` 的 `master` 分支上手动运行 `Deploy website` 工作流，发布官网。
本地验证时使用相同的根路径构建参数：

```bash
NEXT_PUBLIC_BASE_PATH= \
NEXT_PUBLIC_SITE_URL=https://powercontext.oceanbase.io \
NEXT_PUBLIC_REPOSITORY_URL=https://github.com/oceanbase/powercontext \
pnpm build
```

把 `out` 的内容部署到 `https://powercontext.oceanbase.io/`。这些参数在构建时写入页面和资源路径。

`pnpm verify:export` 应使用与构建相同的环境变量；它会检查页面链接、静态资源、跳转页和双语首页的规范地址。

## 生成内容

- OpenAPI 页面由 `openapi/powercontext.yaml` 直接生成。
- Python API 使用 Fumadocs 官方 `fumadocs-python` 与 Griffe 生成。
- Python API 仅展开 7 个公开模块；generated HTTP models 由 OpenAPI 页面承担，避免生成数千个类页面。

`fumadocs-python` 当前仍由官方标记为 experimental。升级时需要验证公开 API 白名单、生成页数与交叉链接。
