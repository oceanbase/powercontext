# PowerContext website

基于 Next.js 和 Fumadocs 的 PowerContext 双语官网。

## 本地运行

需要 Node.js 22、pnpm 11、uv。

```bash
cd website
pnpm install
pnpm dev
```

## 验证

在 `website/` 下运行：

```bash
pnpm lint
pnpm test
pnpm build
```

静态产物输出到 `website/out/`。

## 内容来源

- 双语内容：`docs/en/`、`docs/zh/`。
- API 参考：由 `openapi/powercontext.yaml` 和 `src/powercontext/` 自动生成。
- 修改源文件，不要编辑 `website/content/docs/`、`website/.generated/` 或 `website/out/`。

## 发布

在 `oceanbase/powercontext` 的 `master` 分支上手动运行 `Deploy website` 工作流，
发布到 [powercontext.oceanbase.io](https://powercontext.oceanbase.io/)。
