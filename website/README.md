# PowerContext Fumadocs PoC

这是一个与现有 Zensical 站点隔离的 Fumadocs PoC，用于评估成熟文档组件、品牌化官网页面、双语内容、OpenAPI 与 Python API 文档。

## 边界

- `docs/en` 与 `docs/zh` 仍是源内容，PoC 不移动或修改它们。
- 构建脚本只把 `docs/<locale>/docs` 复制到被 Git 忽略的 `website/content/docs`，供 MDX 编译。
- 现有 `zensical.toml`、主题目录与文档构建命令保持不变。
- meetings 不进入 PoC 的导航或内容源。

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
- Python PoC 仅展开 7 个公开模块；generated HTTP models 由 OpenAPI 页面承担，避免生成数千个类页面。

`fumadocs-python` 当前仍由官方标记为 experimental。PoC 证明它能完成静态构建，但正式采用前仍应验证版本升级策略、公开 API 白名单与交叉链接稳定性。
