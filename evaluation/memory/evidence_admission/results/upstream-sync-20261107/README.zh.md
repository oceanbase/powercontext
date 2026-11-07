# 上游整合验证凭据

本目录记录整合 `b8b189091e7a412300cf1292531208a8ead0e325` 后的工程检查。
它不替换原线上结果，也不形成新的生成质量结论。评估决策仍为 `insufficient_evidence`。

- [integration.json](integration.json)：整合目标、冲突解决、本地代码提交与发布边界。
- [integrity.json](integrity.json)：原交付、原 Git 冻结依赖、生产源码边界与原有网站文件的完整性。
- [validation.json](validation.json)：实际命令、结果、失败后的修复验证及未执行范围。
- [packaging.json](packaging.json)：本地构建的 wheel、源码包哈希及评估包、数据和命令入口检查。
- [artifact-index.json](artifact-index.json)：本目录与当前 PR 说明的独立字节索引。

当前审查入口为 [PR 正文](../../PR_DESCRIPTION.zh.md)和[上游整合说明](../../UPSTREAM_INTEGRATION.zh.md)。
原线上交付与审阅包继续由各自原索引保护；不得修改旧哈希来适配当前运行时。

## 重现边界

在当前检出中可以运行无模型回归、静态检查和打包：

```sh
uv run --locked --project evaluation pytest evaluation/memory/evidence_admission/tests -m "not live" -q
uv run --locked --project evaluation ruff check evaluation/memory/evidence_admission
uv run --locked --project evaluation ruff format --check evaluation/memory/evidence_admission
uv run --locked --project evaluation ty check --project evaluation
make check
PYTEST_WORKERS=4 make test
uv build --out-dir /tmp/powercontext-runtime-build
uv build --project evaluation --out-dir /tmp/powercontext-evaluation-build
```

博客测试复用已存在的静态网站导出。上游更新后，应先构建与该上游一致的导出；旧导出存在并不表示它是最新的。
本次在独立干净检出中执行 `make docs-test`，只同步生成的 `out/` 和 `.generated/`，
保留原检出中已有的网站内容与贡献者文件，再验证主检出的内部链接与完整测试。

重现原线上报告需要原执行提交 `1515a0d11cc2040d0beb341f6626a33c09cce73b`、
原锁定依赖与仓库外的原始私有审计。当前代码应拒绝原会话身份，不能关闭身份校验来重算。
没有私有审计的外部读者只能核验公开汇总及其索引，不能逐响应重放。

本次另在原提交、原锁文件和 Python 3.11 下补验了 v12/v13 的无模型会话测试，43 项通过。
原 v12 Git 守卫测试依赖的忽略目录须事先存在；补验只创建该目录，原代码与断言没有变化。
当前环境的 41 项历史正向跳过与原环境的通过分别报告，不合并为当前运行时的通过数。

默认测试不需要 provider 凭据。本次没有新增线上调用、补写人工语义判断、推送或创建 PR。
完整跨 Python 版本 tox 矩阵与四版本专项检查的范围不同，以 validation.json 的实际记录为准。
