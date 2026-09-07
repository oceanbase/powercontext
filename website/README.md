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
pnpm build
```

静态产物输出到 `website/out`。

## 生成内容

- OpenAPI 页面由 `openapi/powercontext.yaml` 直接生成。
- Python API 使用 Fumadocs 官方 `fumadocs-python` 与 Griffe 生成。
- Python API 仅展开 7 个公开模块；generated HTTP models 由 OpenAPI 页面承担，避免生成数千个类页面。

`fumadocs-python` 当前仍由官方标记为 experimental。升级时需要验证公开 API 白名单、生成页数与交叉链接。
