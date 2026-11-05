---
title: Agent 指引验证方法与记录
description: 执行工具路由与 Skill 评测，判读证据，并查阅历史测量。
---

# Agent 指引验证方法与记录

本文面向验证 PowerContext 指引的贡献者。[工具路由设计](integration-guidance.md)说明 Agent 的预期行为，[分层 Skill](layered-skills.md)说明公共资源与宿主发现方式。

## 测量范围

资源一致性、原生宿主执行和模型行为分别验证。资源检查比较打包文件及其引用，原生测试检查发现与已注册工具，模型评测使用受控返回检查操作选择、参数和最终回答。每份结果仅适用于记录中的宿主、模型、Skill 和契约。

[Claude Code skill-up 套件](https://github.com/oceanbase/powercontext/blob/master/evaluation/skills/skill-up/README.md)和 [ZCode 套件](https://github.com/oceanbase/powercontext/blob/master/evaluation/zcode_guidance/README.md)各自维护环境准备、版本固定和加载／不加载 Skill 的对照运行。执行方式与权限 fixture 见各套件 README。这些结果与原生审批、持久化、记忆质量及任务结果的测量分别报告。

## 准备环境

使用仓库锁定的 Python 开发环境，安装待验证宿主包的依赖。验证 OpenClaw 入口前先构建插件；其固定 SDK 需要 Node 24.15 或更新版本，适配器评测可通过 `POWERCONTEXT_GUIDANCE_NODE` 指定解释器。

真实模型评测从指定环境文件读取 `LLM_MODEL`、`OPENAI_LLM_BASE_URL` 和 `LLM_API_KEY`。凭据及私有转录不提交到仓库。

## 检查打包资源

```sh
make integration-guidance-check
uv run --locked pytest tests/test_layered_skills.py tests/test_guidance_installation.py
```

这些检查覆盖公共副本漂移、本地引用、打包资源和安装，不调用模型，也不能证明所有外部宿主都能发现或执行 Skill。

## 导出原生目录

将 `POWERCONTEXT_GUIDANCE_EXPORT` 设为已存在的目录。以下命令从相应宿主测试导出模型可见的指引、工具和 Skill 元数据：

```sh
mkdir -p /tmp/pc-guidance
export POWERCONTEXT_GUIDANCE_EXPORT=/tmp/pc-guidance
uv run --locked pytest tests/test_mcp.py
pnpm --dir integrations/dsh/plugins/powercontext/tests/runtime install --frozen-lockfile
pnpm --dir integrations/dsh/plugins/powercontext test:e2e:runtime
pnpm --dir integrations/opencode/plugins/powercontext test
pnpm --dir integrations/pi/plugins/powercontext test
pnpm --dir integrations/openclaw/plugins/memory-powercontext test
```

DSH 从真实 SDK 模型请求导出编译后的 schema 和系统上下文，单独运行包注册测试不会导出该目录。Pi 使用 SDK Skill 加载器，OpenClaw 包测试在隔离 profile 中执行原生 `skills list --json`。MCP 初始化通过 FastMCP client 检查。

Hermes 从隔离目录导出原生 `skills_list` 和 `skill_view` 元数据。使用其 CI 固定的 Hermes v2026.8.18（CLI 0.20.4）源码版本，并保留上述导出环境变量：

```sh
git clone https://github.com/NousResearch/hermes-agent.git /tmp/pc-hermes
git -C /tmp/pc-hermes checkout e624e9fde561e1add9388384012b295fde669ade
POWERCONTEXT_HERMES_SOURCE=/tmp/pc-hermes uv run --locked pytest tests/e2e/test_hermes_skills.py
```

DSH、Hermes 的发现名称和描述来自原生目录；评估器补充打包参考文件正文，不覆盖发现元数据。Hermes provider 单元测试不导出原生目录。

## 执行模型评测

```sh
uv run --locked python scripts/evaluate_integration_guidance.py \
  --catalog /tmp/pc-guidance/dsh.json --env-file .env \
  --output /tmp/pc-guidance/results.json --skill-modes loaded unloaded unavailable
```

可重复传入 `--catalog` 覆盖其他宿主。中英文场景包含普通任务、上下文充分、搜索、盘点、保存、预览、Handoff、Review、空检索、写入失败和保存工具缺失。合法的 Scope 解析视为数据操作的前置步骤，工具由模型自行选择。

多轮 Handoff 使用 `--cases handoff handoff_request`，评估器检查准备过程及完整返回载体。普通交接和明确临时交接均不能提交持久里程碑。

检查参考文件读取时，添加 `--layered-skills --skill-modes unloaded unavailable`。可选读取器提供实际打包正文并记录资源请求；`skill_search`、`skill_handoff` 要求在数据操作前读取对应的 Memory、Handoff 参考文件。只读入口、读错领域或操作后补读均不能通过。普通请求不要求读取 Skill；unavailable 模式不提供读取器，检查其余工具指引是否充分。

## 判读执行证据

模型评估器提供受控返回，不执行真实 Server 写入或生成。原生适配器评测对已注册的 Pi、OpenCode 工具和构建后的 OpenClaw 入口提供受控 HTTP 返回，记录转换后的参数及包装结果。fixture 授权不能证明真实交互审批渠道。DSH 在这里使用受控 MCP 返回，其 SDK 目录导出和运行时验收分别验证。MCP 目录也不能证明各产品的原生 Skill 发现。

Handoff 验证要求返回完整的可交接载体，保留 Scope、精确证据、回执和必需的可空字段。可选的 null 元数据可以省略。

模型异常、空输出、截断、连接失败、无效参数和不完整载体均保留为失败。首个工具选对，不能说明流程已完成或最终回答准确。

## 审查结果陈述

`routing_passed`、`arguments_passed` 分别检查操作选择和参数。每条观测初始为 `reporting_passed: null`、`acceptance_passed: false`。审查记录中的调用、受控返回和最终回答，检查是否虚报成功、虚构证据或遗漏未完成的工作。

以每条观测的 `review_key` 为键编写 JSON 对象，值包含布尔型 `passed` 和具体、非空的 `reason`。以下命令应用审查结果，不再次调用模型：

```sh
uv run --locked python scripts/evaluate_integration_guidance.py \
  --review-report /tmp/pc-guidance/results.json \
  --reporting-review /tmp/pc-guidance/reporting-review.json \
  --output /tmp/pc-guidance/reviewed-results.json
```

键由完整观测的哈希生成，调用或回答变化后，旧审查结果失效。真实模型评测在审查前以非零状态退出；审查命令仅在全部观测的路由、参数和结果陈述通过时返回零。运行记录须保留失败、未审查观测、目录和转录。

## 历史测量

这些记录仅适用于各自的版本和契约，不代表当前检出版本已经通过。9 月的模型评测使用 `step-3.7-flash`、温度 0、自动工具选择、虚构的 Aurora 事实和隔离的 `fixture-scope`，由评估器提供受控返回。

| 日期 | 版本与证据 | 测量条件 |
| --- | --- | --- |
| 2026-09-15 | 主分支 `847203dc`；[全部 276 条观测](https://github.com/knqiufan/powercontext/blob/codex/align-powercontext-tool-routing/e2e/integration-guidance/results/step37-native-20260915.jsonl) | 最终对比合并 DSH 的 `qualification` 批次（输出上限 10,000 token）与其余八个入口的 `qualification-final` 批次（16,000 token）。Codex 根据准确转录审查结果陈述。 |
| 2026-09-10 | [全部 Handoff 批次](https://github.com/knqiufan/powercontext/blob/bcfdc9fc726021fce3b20a46f3788096794a2b60/e2e/integration-guidance/results/step37-handoff-20260910.jsonl) | 每个宿主覆盖两个场景、两种语言和三种 Skill 状态，合并各批次中每个条件的最后一次观测。 |
| 2026-09-09 | 基线 `fe4002d37663213294ebffe4c080b6676b1c8014`；[路由证据](https://github.com/oceanbase/powercontext/blob/master/e2e/integration-guidance/results/step37-20260909.jsonl) | Windows、配置的 StepFun endpoint、输出上限 6,000 token。初始矩阵覆盖 11 个中英文场景，分别加载和不加载 Skill。 |

### 模型行为测量

各列衡量的行为不同：9 月 9 日仅检查首个操作选择，9 月 10 日要求完整 Handoff 序列，9 月 15 日进一步审查与转录绑定的结果陈述。横线表示该入口未参与相应对比。

| 入口 | 9 月 9 日：路由 | 9 月 10 日：完整 Handoff | 9 月 15 日：序列与载体 | 9 月 15 日：审查结果陈述后 |
| --- | --- | --- | --- | --- |
| DSH 基线 | 36/44 | - | - | - |
| DSH 实现 | 41/44 | 5/12 | 12/12 | 12/12 |
| Pi | 42/44 | 5/12 | 12/12 | 12/12 |
| OpenCode | 41/44 | 3/12 | 12/12 | 12/12 |
| OpenClaw | 34/44 | - | 12/12 | 11/12 |
| Hermes | 41/44 | 10/12 | 12/12 | 12/12 |
| Codex MCP 目录 | 38/44 | 12/12 | 12/12 | 12/12 |
| Claude Code MCP 目录 | 39/44 | 11/12 | 11/12 | 11/12 |
| WorkBuddy MCP 目录 | 38/44 | 8/12 | 12/12 | 12/12 |
| 可移植 Agent Plugin 配合 MCP | 35/44 | 10/12 | 11/12 | 11/12 |
| 合计 | - | 64/96 | 106/108 | 105/108 |

9 月 15 日对比中的两个 Handoff 失败将没有证据的事实标为已验证。结果审查还拒绝了 OpenClaw 将“没有代码变更”加强为“不需要代码变更”的回答。另一个 DSH/Codex 结果陈述样本通过 17/24 次选择，审查后通过 16/24：全部 12 个写入失败场景都说明未保存，七个工具缺失场景失败，另一个回答建议了未经授权的 Source 捕获。这些失败保留在原始证据中。

### 多轮 Handoff 验证

9 月 10 日的样本仍未通过验收。失败包括无效的事实依据、格式错误或未完成的 Draft，以及不完整或被改写的载体。当时的载体检查还会拒绝省略可空字段的回答。最终 96 条观测没有出现 commit，但较早失败会截断序列，不能证明成功继续执行时的行为。

DSH 最终批次使用真实 SDK 编译后的模型请求，中间批次从注册声明导出目录。这里所有模型调用均使用受控返回。原生适配器检查验证参数和响应处理，不能证明真实持久化、生成或交互审批。

9 月 9 日的 DSH 样本中，普通任务及数据流程的十类场景通过 40/40；三个保存工具缺失场景尝试调用了不可用工具。最初的路由分类器还曾拒绝部分合法的底层 Source 捕获选择。公开分数保留原始观测。

### 执行与回归证据

| 执行记录 | 观察到的行为 | 适用限制 |
| --- | --- | --- |
| DSH SDK/host `0.1.2-rc.1` | 系统指引和 19 个 PowerContext 原生工具在加载 Skill 前到达模型；测试覆盖召回、注入、重启恢复、直接错误和 Scope 隔离。 | 仅适用于该宿主版本。 |
| Step 3.7 通过真实 DSH | 模型选择 `pc_remember`，宿主因交互审批不可用而拒绝。模型说明未保存，Server 检查确认没有写入；随后明确搜索找到了预置 Memory 及精确引用。 | 未强制工具选择；观察的是拒绝写入和搜索。 |
| Codex CLI `0.153.4`，配置模型 `gpt-6-astra` | 实际保存 Memory、随后搜索，以及 MCP endpoint 不可用时不虚报成功。 | 仅适用于记录中的桌面应用自带 CLI 和模型。 |

### 验证边界

9 月的其他路由样本保留以下较窄的测量结果：

| 样本 | 结果 | 适用限制 |
| --- | --- | --- |
| 三种 Skill 状态下的边界场景 | WorkBuddy 30/30；Agent Plugin 29/30；OpenClaw 27/30 | 上下文充分、盘点、Handoff、预览和空搜索。 |
| DSH、Pi、OpenCode、Hermes、Codex 和 Claude Code 的 Skill 不可用场景 | 125/132 | 剩余失败包括多余检索、过早准备和调用缺失工具。 |
| OpenClaw 根据实际可用工具生成的目录 | 1/2 | 保存工具缺失场景。 |
| Handoff 与预览的描述边界 | 48/48 | OpenClaw、Hermes、OpenCode、Agent Plugin 的首步选择；未继续执行工具返回或检查后续写入。 |

9 月 9 日的测量中，OpenClaw 没有打包 Skill，其 Skill 状态标签表示重复目录条件。MCP 目录共享 Server 工具，不能证明分别执行了各宿主的原生 CLI 或发现了 Skill。首步选择分数不能证明完成 finalize，或正确区分临时交接与持久提交。运行时仍须执行权限检查；生成文字不能证明已经批准、发布、安装或执行。
