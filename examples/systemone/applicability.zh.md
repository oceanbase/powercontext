# 使用 Jev / Laya 判断 Experience 和 Skill 是否适用

[English](applicability.README.md) · [Issue #1647](https://github.com/oceanbase/powercontext/issues/1647)

这个功能解决的是“当前任务应该参考哪些经验、使用哪个 Skill”。例如修改 HTTP 契约时，
生成客户端和验证契约两条经验都适用；只解释接口用途时，这些修改流程都不该被推荐。

实现提供了可以复用的 `ApplicabilitySelector` 接口和 `DecisionApplicabilitySelector` 实现。
底层沿用项目已有的 `DecisionModel`，Jev / Laya 接线复用 #1797 的 System One 适配器。
代码放在示例层，不会改变 HTTP、MCP、PreparedContext、存储格式或 Runtime 默认行为。

## 如何选择

1. `ServerCandidateCatalog` 使用调用者已认证的 `PowerContextClient` 检索已审批、仍有效的
   Experience 和 Skill。Skill 只读取有限的候选包，待审批、已拒绝和已退役的内容不会进入候选池。
2. 校验 Skill 的标准包与已审批版本一致，调用项目已有的兼容性检查。不兼容、环境未知或需要
   人工检查的包先被排除。完整文本证据包含 `scripts/` 之外的 JSON 和 YAML 支持文件，
   与入口文件一起保留原文。没有标准包的旧 Skill 和包含非 UTF-8 文本引用的包也会明确排除。
3. 对每条候选分别判断是否适用，名称相近还不够。确定适用返回 `yes`，确定不适用返回 `no`，
   必要条件未知返回 `abstain`。适用的 Experience 全部保留，不会把互补经验限制成一条。
4. 只在已经适用的 Skill 之间比较哪个更具体，推荐一个 Skill。无法区分时保留原检索顺序。
   模型置信度只作记录，不作为跨候选分数排序。
5. 返回精确的 Scope、Artifact ID、revision、内容和包摘要，加载前再次检查。
   内容更新或失效会拒绝旧推荐，不会自动换成新版本。

没有适用候选时返回 `none`，存在未知条件而没有明确适用候选时返回 `uncertain`。
默认关闭模型调用。后端缺失、调用失败或总时限耗尽时，保留相同的检索基线并标记
`used_fallback=True`。宿主应该使用原有策略处理回退，不能把它当成模型确认适用。

本实验明确规定检索基线为“保留检索到的全部 Experience 和第一个合格 Skill”。
它衡量检索顺序与适用性选择的差异，没有声称复现原生宿主自主选择 Skill 的效果。
PreparedContext 仍不包含 Skill。

接口使用代码见 [英文说明](applicability.README.md#reuse-the-interface)。构造候选对象不等于获得
访问权限。应用必须提供当前身份的 SDK 和实际观察到的宿主环境，重新检查也不是执行授权。
发布、安装、加载和执行仍由宿主负责，遵守既有审批规则。

默认最多检索两条 Experience 和八个 Skill，选择器最多接受 16 条候选。
最多调用 N 次适用性判断和 N−1 次 Skill 比较，总时限为 60 秒。
每次判断最多接受 24,000 字节的完整证据，超限会明确返回未知，不会截掉必要条件。
底层 System One / Laya 的输入限制同样生效。加载前检查会重复有限的检索和包下载，
示例没有验证整个库的检索性能。选择器和目录读取不会执行包内程序。

## 配置并运行真实接口测试

在项目根目录执行 `uv sync --locked`，把 `examples/systemone/jev.env.example` 复制为 `.env_jev`。
使用 Laya 时复制 `laya.env.example` 为 `.env_laya`，Git 已忽略这些 `.env_*` 文件。

| 配置 | 含义 |
| --- | --- |
| `SYSTEMONE_PROVIDER` | `jev` 或 `laya` |
| `SYSTEMONE_ENDPOINT` | 完整决策接口地址：OpenRouter 使用 `/api/alpha/decisions`，Laya 使用 `/v1/systemone` |
| `SYSTEMONE_MODEL` | 实际部署的模型 ID，优先使用服务方确认的固定版本 |
| `SYSTEMONE_API_KEY` | 接口凭据，本地无认证 Laya 可以留空 |

Jev 模板已配置 OpenRouter 和带版本号的模型 ID。在本地 `.env_jev` 中填写自己的
OpenRouter key 即可，凭据不要提交到 Git：

```dotenv
SYSTEMONE_PROVIDER=jev
SYSTEMONE_ENDPOINT=https://openrouter.ai/api/alpha/decisions
SYSTEMONE_MODEL=typesafe/jev-1.13
SYSTEMONE_API_KEY=
```

请求使用 Bearer 认证，JSON 包含 `model`、`state` 和 `questions`。选择器使用 `choice` 返回
`yes`、`no`、`abstain`，分别表示适用、不适用和信息不足。单独的 `noul` 概率不能表达这里
要求的第三种结果。可选的网站归属请求头不必配置。协议见
[OpenRouter 官方说明](https://openrouter.ai/blog/insights/what-is-jev/)。

```text
uv run --locked python -m examples.systemone.applicability_eval --env-file .env_jev
uv run --locked python -m examples.systemone.applicability_eval --env-file .env_laya --checkpoint /path/to/laya-checkpoint
```

Laya 必须提供与服务端模型一致的 checkpoint，可选依赖见 [System One 说明](README.md)。
测试会创建全新的本地 SQLite 数据库和 Scope，通过提案、显式审批 API 写入合成候选。
SDK 使用 ASGITransport 调用真实 Server，权限测试另行使用强制鉴权和合成身份验证权限边界。
这些测试都不会读取日常项目记忆。

十个固定中英文案例覆盖修改契约、相近但具体程度不同的 Skill、多条互补经验、只问解释、
不相关任务、审批未知和明确拒绝。两组比较使用完全相同的任务、环境和候选池。
Server 测试还覆盖操作系统、Python 版本不匹配及版本无法确定时的过滤。

结果写入 `.powercontext/applicability/<run-id>/`。报告包含精确引用、完整合成输入、模型标识、
问题和案例版本、源码摘要、判断、比较、排除原因、耗时及用量。真实宿主运行前先保存已完成的
选择结果，中断后仍可查看各案例文件。重新执行会创建新实验并重新请求服务，不会恢复付费请求。
报告不包含 endpoint 或 key。

报告中的模型名是配置值，`model_version_verified=False` 表示示例无法替服务方保证别名不会变。
需要向服务方核实固定模型 ID，示例不会凭名字猜测版本已固定。

## 可选的真实 Codex 流程

安装并登录 Codex CLI 后执行：

```text
uv run --locked python -m examples.systemone.applicability_eval --env-file .env_jev --codex-host
```

两组 `change-en` 测试各用一个隔离目录。宿主先读取推荐的精确文本，
再修改小型 HTTP 契约、生成 Python 客户端、运行契约测试，两组任务和初始文件相同。
测试程序独立检查响应字段和生成代码，并确认生成器、测试及选择内容没有被修改。
沿用现有 Codex 认证、配置和审批规则，没有绕过沙箱或审批。
`--codex-timeout` 限制每次 CLI 运行，默认 180 秒。

程序通过 `PATH` 查找 `codex`。如果 CLI 提示桌面应用中可用的模型不受支持，先检查
`codex --version`；本机的 CLI 与桌面应用可能使用不同版本。
CLI 子进程使用自己的工具连接和会话标识，保留现有认证、代理、配置和审批设置。

这验证的是实际 CLI 和工具执行，上下文由宿主显式读取，没有验证原生 Skill 自动发现或发布。
测试不会安装 Skill，也不会执行包内脚本。使用既有 `record_skill_usage` API 保存精确 Skill
版本、包摘要和任务结果 Source。只有观察到读取上下文、生成代码和通过契约测试的命令，
且选中的是精确的已知生成或维护案例包，才记录 `invoked=true`。
命令可以运行案例的 `.py` 文件，也可以使用等价的 `python -m` 模块入口。检查会识别 Python
实际启动的目标，并要求成功退出、输出标记及独立的工作目录验证；无关命令中引用文件名不算执行。
其他 Skill 的调用和结果仍为未知：生成客户端不能证明执行了部署，未选择 Skill 也不能算调用。
仅有推荐或宿主口头声称完成时，调用情况仍为未知。
JSONL 宿主日志和独立检查为这些记录提供依据。

Skill 使用记录属于已注册的 adapter Source，可以通过现有 `context.sources` 的类型化目录和
读取 API 查看。当前通用 HTTP Source 读取和 Review 文本展开不支持此类型，示例不会扩展这些契约。

## 报告怎样看

| 指标 | 含义 |
| --- | --- |
| `applicable_candidate_recall` | 已检索候选中真正适用的候选被绝对适用性判断保留的比例，与 Skill 最终排序分开计算 |
| `unretrieved_applicable_candidates` | 已知适用但没被检索到的案例候选数量 |
| `wrong_recommendations` | 推荐了已知不适用的候选数量 |
| `unnecessary_recommendations` | 明确无适用项的任务仍收到多少推荐 |
| `selection_exact` | 推荐及结果符合案例预期，并且未发生回退 |
| `wrong_loads`、`unnecessary_loads` | 观察到真实宿主读取时才记录的实际数量，其余为未知 |
| `task_success` | `change-en` 真实宿主任务的独立验证结果，仅做选择的案例为未知 |
| `added_latency_ms` | 选择器相对检索顺序基线增加的耗时，不含公共检索和宿主运行 |
| `estimated_added_cost_usd` | 用量乘以用户提供的价格，缺用量、缺价格、回退或中断时为未知 |

可以通过 `--input-price-per-million` 和 `--output-price-per-million` 提供服务方现行价格。
Codex 宿主耗时和用量另行记录，不会推测其美元费用。模拟测试只能验证协议和报告行为，
不能证明 Jev / Laya 的准确率。这组小型合成案例用于分别检查拒绝、未知及相对偏好，
不代表生产校准或普遍质量结论。真实接口和宿主结果需要用实际运行报告说明，失败也应保留。
如果辅助选择不符合已知答案、发生回退或启用的宿主测试失败，CLI 会以状态 1 退出。
报告仍然保留，便于检查原因。

离线验证命令见 [英文说明](applicability.README.md#offline-validation)。
