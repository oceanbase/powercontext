---
title: 维护网站元数据与 Markdown 导出
description: 修改网站内容或路由时，更新搜索摘要、链接分享预览和内容发现入口。
---

# 维护网站元数据与 Markdown 导出

修改网站内容、新增页面或更换部署地址时，按本页步骤更新对外信息。Fumadocs 网站为 GitHub Pages 构建 HTML、元数据和 Markdown 文件，部署后由静态托管服务提供这些文件。

## 修改页面标题和摘要

编辑 `docs/en/` 和 `docs/zh/` 中的源 Markdown。产品文档、开发指南和 RFC 各自使用文件开头的 front matter。博客文章按[博客发布指南](https://github.com/oceanbase/powercontext/blob/master/docs/zh/blog/publishing.md)同步修改中英文。构建会把源文件复制到 `website/content/`，内容修改应落在原文件中。

摘要应帮助读者判断是否需要打开这页：

| 页面类型 | 摘要应说明什么 |
| --- | --- |
| 入门教程 | 跟随教程能完成什么 |
| 操作指南或迁移指南 | 要完成的任务、影响适用性的前提和预期结果 |
| 参考文档 | 可以查询哪些接口、字段或规则 |
| 原理说明 | 回答什么问题、解释哪些设计取舍 |
| RFC | 提议的设计及其范围 |
| 博客文章 | 正文讨论的问题或论点 |

使用正文中的术语，保留影响安全和兼容性的前提，并区分设计提案与已发布能力。搜索摘要应概括页面内容，避免堆砌产品关键词。

独立页面的标题和摘要在路由文件的 `pageMetadata` 调用中修改。站点简介位于 `website/src/lib/metadata.ts`。博客文章使用文章摘要，作者页说明该作者发表的文章，归档页标明发表年份，分页标题包含页码。

## 将新路由接入搜索和 Markdown 入口

调用 `pageMetadata` 时填写页面路径和语言，只为实际存在的对应页面提供语言关联。文档路由通过 `contentMetadata` 查找对应译文。英文首页的 canonical 为 `/`，访问 `/en/` 时也使用它；其他页面的 canonical 以 `/` 结尾。

在 `website/src/app/sitemap.ts` 中登记独立页面。文档、博客、Python 和 HTTP API 参考通过各自的内容源进入站点地图。站点地图排除重复英文首页、旧重定向和未发布的博客。只有记录中存在发布或更新日期时才填写 `lastmod`，重新构建不能说明内容何时更新。`website/src/app/robots.ts` 声明站点地图地址并允许抓取。

文档和已发布博客也会进入 Markdown 导出。HTML 元数据链接到对应 Markdown，页面操作支持复制和打开。Python 与 HTTP API 参考提供 HTML 页面和站点地图条目，不属于 Markdown 内容集合。

## 修改分享图和结构化数据

在 `website/src/app/og.png/route.tsx` 中修改默认分享图。构建使用已有品牌 Logo 生成 1200 × 630 的 PNG。文案应简短，便于在链接预览中阅读。单页分享图通过 `pageMetadata` 设置，已发布博客使用文章封面。站点图标位于 `website/public/icon.svg`。

文章的 `BlogPosting` JSON-LD 应与可见页面使用相同事实，包括标题、摘要、作者主页、封面、发布日期和更新日期。文档和博客文章还提供面包屑 JSON-LD。Fumadocs 搜索索引中的 `structuredData` 用于站内搜索的标题和正文索引，与 JSON-LD 分别维护。

## 使用部署地址构建

将 `NEXT_PUBLIC_SITE_URL` 设为站点源地址，将 `NEXT_PUBLIC_BASE_PATH` 设为部署子路径；部署在根目录时，base path 留空。生成绝对 URL 时会使用这两个值。采用子路径部署时，还需在托管服务中配置源站根目录的 `robots.txt`，让爬虫可以找到站点地图。

在仓库根目录构建并检查类型：

```sh
make docs-build
pnpm --dir website types:check
```

构建会检查本地链接和静态导出。在 `website/out/` 中核对产物：

| 产物 | 核对内容 |
| --- | --- |
| 页面 HTML | 标题、摘要、canonical、已有译文、分享元数据和 JSON-LD |
| `sitemap.xml` 与 `robots.txt` | 部署地址和公开页面范围 |
| `og.png` 与 `icon.svg` | 图片可打开，文案完整可读 |
| `llms.txt` 与 `llms-full.txt` | 默认英文索引和全文 |
| `en/llms.txt`、`zh/llms.txt` 及各自的 `llms-full.txt` | 索引和全文使用对应语言 |
| `{lang}/llms.mdx/{section}/{slug}/content.md` | 页面内容及其 HTML 来源链接 |

Markdown 的 section 为 `docs`、`development`、`rfcs` 或 `blog`，概览页省略 `{slug}`。`website/src/lib/llms.ts` 通过 Fumadocs loader 合并文档与已发布博客，官方 `llms()` API 从页面树生成索引，并通过 `getText('processed')` 生成单页和全文。URL helper 与 `content.md` 路由采用 [Fumadocs LLM 集成](https://www.fumadocs.dev/docs/integrations/llms)中的做法。

静态托管直接提供导出的 Markdown 文件。Next.js rewrite 和 `Accept` 请求头协商需要服务端，本站不启用这些功能。相对文档链接通过 Fumadocs 内容源解析为绝对 Markdown 地址，保留查询参数和锚点；以 `/` 开头的链接和图片使用部署地址。代码示例和 Markdown 源文件保持原样。添加页面标题与来源 URL 前，会移除正文已有的开头标题。MDX 组件按 Fumadocs 默认行为保留 JSX，不另行转换为自定义文本格式。

源文件直接使用 UTF-8 文字。路由声明 UTF-8 内容类型，静态托管也需为 `.txt` 和 `.md` 文件返回带 UTF-8 charset 的响应头。排查乱码时，检查预览或部署环境的实际响应头。导出文件便于工具读取文档，搜索引擎自行决定是否收录或引用。

核对的构建应使用发布环境的变量。准备好发布时，再通过网站部署工作流部署这些文件。
