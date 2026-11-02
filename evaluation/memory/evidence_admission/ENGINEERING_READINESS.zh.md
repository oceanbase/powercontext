# 工程验证与实验边界

issue #2074 的必需工程检查已通过。工程检查不代表模型质量或正式联合比较通过；总体决策仍为
`insufficient_evidence`，v15 的 Unicode 单次请求门槛失败保留。生产运行时代码、公共 API 和历史结果没有修改。
机器凭据见 [工程汇总](results/engineering-readiness/summary.json)。

## 检查结果

| Python | 根包测试、doctest、覆盖率 | 根包类型 | 完整 evaluation 测试 | evaluation 类型 |
| --- | --- | --- | --- | --- |
| 3.11.15 | 7053 passed / 627 skipped | 通过 | 1291 passed / 1 skipped | 通过 |
| 3.12.3 | 7053 passed / 627 skipped | 通过 | 1291 passed / 1 skipped | 通过 |
| 3.13.13 | 7053 passed / 627 skipped | 通过 | 1291 passed / 1 skipped | 通过 |
| 3.14.5 | 7053 passed / 627 skipped | 通过 | 1291 passed / 1 skipped | 通过 |

`make check` 的锁文件、完整 hooks、根包和 Pydantic AI 集成类型检查、50 项集成清单测试通过。
`make test PYTEST_WORKERS=4` 为 7053 passed、627 skipped。issue 指定目录的 Ruff 检查和格式检查通过，
整个 evaluation 的类型检查通过。根包与 evaluation 的 wheel、sdist 均构建成功。

根包第一次 Python 3.12 全量运行有两项失败：测试启动器在系统解释器内使用 `site.addsitedir`，
系统旧版 `typing_extensions` 的优先级高于选定环境，导致子进程无法导入锁定的依赖。
修复依赖路径顺序后，该版本完整重跑通过；四个版本的对应两项最小回归均通过。
其他三个版本的完整根包运行在这项测试夹具修复前已启动，随后验证了修复后的同一行为。
原失败记录保留，不将它描述为从未失败的单次矩阵。

串行根包尝试中止后，以 pytest-xdist 重跑相同的全量测试与覆盖率。
中止记录单列为 cancelled，不计通过。各 tox 环境使用独立项目环境、覆盖率数据和 XML 文件，
并输出实际解释器版本，避免多个版本复用同一个 `.venv`。

## 修复范围

- 历史 v14 合同仅豁免 JSON 美化，仍接受 JSON 语法检查，并由独立 hook 校验完整原始字节的 SHA-256。
  `.gitattributes` 禁止该文件的换行转换；实际 Git 过滤器回放证明 `core.autocrlf=true` 时仍保持原字节。
  原始哈希为 `ea129d7358767d462163f042c20e8d6e81f0a6ab30f01e956709acec1349832b`。
- LongMemEval 通过显式可选 evidence 能力协议完成类型收窄，缺失或不可调用时仍拒绝执行。
  没有给 `ScopedAtomicMemory` 增加 evidence API；原生 lineage 验收仍因 #1975 缺失而跳过。
- v12/v13 的离线 mock 回归将 Python、SQLite 和 Git 派生发行版本标签与内容身份分开记录。
  代码、锁文件、SDK、schema 等身份仍须完全匹配，实际 Source 重放后的请求绑定与成本也须一致。
  live 模式继续要求完整执行身份一致，不使用这一离线兼容例外。
- MCP 计数读取兼容解析器返回整数，继续拒绝非有限、负数或非整数计数。
- v15 CLI 延迟载入 Git 依赖，使安装包中的 `--help` 可用；执行冻结或比较仍明确要求 Git 源码仓库。
  独立环境的实际 wheel 安装、仓库外合成清单读取和 CLI 行为已验证。
- 构建忽略 `.pytest-tmp/`，避免把本地解释器和测试临时文件误收进 sdist。
- Python 3.12 测试启动器优先解析选定环境，再执行 `.pth` 导入；原状态、取消及响应时间断言保留。

## 历史与预算

历史 v14 模板经真实 Pipeline 离线回放：exact-literals 保持 4/4；Unicode 保持 1/3，
且原估算 6333 大于 4096 的失败仍保留。最早 v14 模板缺失，不能完整回放。
v15 原有四份公开结果的哈希全部保持不变。见 [历史回放](results/engineering-readiness/historical-replay.json)。

用户已明确 ¥5 条件预算从批准后的 v9 调用开始累计。该范围内七次已知调用按冻结高峰、全未命中费率
重算的上界为 ¥0.090520，条件余量为 ¥4.909480。更早的两次 smoke 与 v5 共 575 次尝试独立记录，
其中 562 次有已知用量、13 次失败的 usage 缺失；未知用量不按零计，也不伪造完整账户账单。
详见 [历史费用清单](results/engineering-readiness/historical-cost-inventory.json)。

[官方价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)核验的高峰费率为输入 ¥2/百万 token、
输出 ¥8/百万 token；旧请求名 `deepseek-v4-flash` 当前由 V4.1 Flash 路由服务。
记录请求别名不等于固定物理模型快照。费率、检索时间与保守预留见
[提供商记录](results/engineering-readiness/provider-rates.json)。本工程阶段新增真实生成调用为零。

## 限制与后续门槛

这些结果来自 Linux；没有实际 Windows、OceanBase、远端取消或真实生产工作器的执行结论。
Git 的换行过滤验证不替代 Windows 宿主测试。覆盖率工具对已删除的临时迁移文件产生非致命警告，
不声称警告为零。额外的整个 evaluation 目录 Ruff 扫描仍有本 issue 范围外的既有诊断；
issue 指定目录的 Ruff 和仓库完整 hooks 已通过。

工程通过只允许进入独立协议的离线研究。新协议仍须固定单元规则、评分与预算合同，完成失败回放、
三集全部 1620 条比较及最小清单准入；全部通过并完成提交冻结、SDK/Pipeline 预检与预算预留后，
才可逐例开展精确字面量和 Unicode 的真实小样本验证。正式联合比较与生产后续均未获本报告授权。
