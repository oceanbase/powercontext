# Experience 输入编码评测

这套评测检验同一份公开工程证据，经 `off`、`shadow`、`active` 三种输入编码模式后，能否生成可靠、可再次使用的 Experience，以及整个流程实际消耗多少 Token。它使用真实 Runtime 的 Source 写入、Experience 生成、Review 批准、Artifact 读取和关闭重开后的读取。

**这是基于 SWE-bench Pro 历史修复证据的 Experience 评测，不是 SWE-bench 解题成绩。** 这里给模型的 gold patch 是待总结的已公开参考材料，没有执行原仓库测试，也不能把测试代码的存在说成测试通过。代码路径覆盖率只是辅助诊断，不能代替语义质量。

## 固定数据与场景

- 复用现有 `evaluation/coding/swebench_pro` 的 `SweBenchProCatalog`，强制校验公开 v2 数据的 731 条记录及 SHA-256：`b5b2462bfbf5aeb2cb7ba7d215778a1768b85f9d7ad7f748546c7f80a0ad1510`。
- 原件是 [SWE-bench Pro 固定版本的公开 JSONL](https://github.com/scaleapi/SWE-bench_Pro-os/blob/ca10a60a5fcae51e6948ffe1485d4153d421e6c5/helper_code/sweap_eval_full_v2.jsonl)。本地默认位置是 `/data/swebench/cache/swebench-pro.git/helper_code/sweap_eval_full_v2.jsonl`，可用 `--dataset` 覆盖。
- 来源仓库的工具采用 MIT 许可证；其 Hugging Face 数据卡没有独立声明数据许可证。保留上游出处和原始代码归属，不把 harness 的许可证等同于全部原始仓库代码的许可证。
- 离线阶段覆盖全部 731 条，超出生产输入边界的任务也保留在报告中。
- 真实阶段使用现有 `swebench-pro-stability-v1` 的固定 24 条、多仓库切片，默认每种模式重复两次，共 144 个生成任务。24 条并不是全量 731 条的无偏随机样本。
- 每条任务依次拆分 issue、reference patch、reference tests，每段最多 12,000 字符，内容不重复、不改写。超过 32 个证据引用的任务明确失败；不通过静默截断制造更好成绩。
- Source metadata 明确标注 `test_execution=not_executed_by_this_evaluation`。各模式使用相同的 Source ID、内容和顺序，在独立 Scope 中执行。

RTK 的参考点是同时保留原始/优化输出、负收益与真实会话用量，不能把 `bytes/4` 当作账单；[Headroom 的参考点](https://github.com/headroomlabs-ai/headroom/blob/main/benchmarks/compression_benchmark.py)是同时报告 Token 与答案准确性，计入摘要调用成本、耗时及失败。本套件不运行 RTK 或 Headroom，因此不提供与它们的胜负比较。

## 执行

使用根项目 Python 环境，从仓库根目录执行。产物放在 `/data/codex-tmp/` 的独立目录中。离线阶段无需 `.env`、数据库或模型：

```bash
export TMPDIR=/data/codex-tmp
.venv/bin/python -m evaluation.performance.input_encoding offline \
  --output /data/codex-tmp/input-encoding/offline-v2.json --iterations 3
```

它按生产 `ContentSource → GenerationEvidence → ArtifactGenerationInput` 形状测量，验证严格还原、收益选择、回退原因及局部编码耗时。局部字符估算和算法耗时不代表供应商计费或端到端延迟。每次运行先做一次预热，随后记录 `--iterations` 次测量。

可选的连通性基线探针位于 `tests/e2e/rfc2086_real_acceptance.py`：

```bash
.venv/bin/python -m tests.e2e.rfc2086_real_acceptance \
  --env-file /absolute/path/.env --output /data/codex-tmp/input-encoding/provider-baseline
```

它只读检查既有数据库，并向真实 OpenAI Chat 兼容服务提交合成加法请求。该探针使用未启用 enforcing 的独立客户端，
成功仅说明本次数据库连通性及结构化输出可用，不能证明隐私保护、Experience 质量或编码节省。其费用与结果应单独记录。
配置的 OceanBase 数据库不存在时会报错，不会创建数据库或改用其他数据库。

先执行一条真实服务 smoke，再建立独立目录运行完整固定切片：

```bash
.venv/bin/python -m evaluation.performance.input_encoding run \
  --env-file /absolute/path/.env \
  --output /data/codex-tmp/input-encoding/smoke --limit 1 --repeats 1

.venv/bin/python -m evaluation.performance.input_encoding run \
  --env-file /absolute/path/.env \
  --output /data/codex-tmp/input-encoding/main --repeats 2 --concurrency 1

.venv/bin/python -m evaluation.performance.input_encoding judge \
  --env-file /absolute/path/.env \
  --protection-config /private/protection.json --max-output-tokens 4096 \
  --reference-questions /data/codex-tmp/input-encoding/reference-questions.json \
  --output /data/codex-tmp/input-encoding/main --concurrency 2
```

`run` 使用 `.env` 配置的数据库与 LLM；SQLite 则使用输出目录中的独立 `experience.db`。可通过 `--database-config /private/database.json` 指向另一个已准备好的评测数据库，JSON 采用 `BuiltinConfig.database` 的现有形状。本评测进程仅执行显式操作，具体后台隔离方式见下文。数据库的处理能力清单必须与所用配置兼容；套件不创建或删除外部数据库，不修改已有业务 Scope。评测产生的专用 Scope 会保留供复查。

OceanBase 使用 `artifact_processing_role=api`，保留原部署能力清单及调度声明，但本评测进程不创建后台 worker 或 supervisor；SQLite/seekdb 使用空处理能力清单。已有数据库的向量索引维度和类型也必须匹配配置。独立评测数据库可以避免其他已有后台进程消费新增 Source。

用 `--protection-config /private/protection.json` 对三个模式统一应用现有保护配置，用 `--max-output-tokens 4096` 统一设置输出上限，两者都进入实验身份。保护配置必须对应实际协议、endpoint、模型响应别名和部署预算；套件不会自行声称某个 tokenizer 上界已获验证。需要更换 SDK 的明确协议前缀时，可用私密 `.env` 副本，报告应披露最终模型和协议。主实验与评分应使用同一份副本。

额外适用性切片可以通过 `--case-ids /path/to/public-ids.json --cohort conditional-applicable-v1` 指定固定公开 ID 数组。ID 必须属于同一验证过的 731 条原件且不能重复；实际选择及摘要写入 manifest。主切片始终保留原来的 24 条；额外挑选可压缩任务的结果仅表示条件适用性，不能替代全量覆盖率或主切片结论。

以相同切片、相同模型设置，在三个新输出目录分别用 `--concurrency 1`、`4`、`8` 运行负载测试。生成顺序在每个 case/repeat 内固定种子随机交错三种模式；`shadow` 应保留原始模型输入，可作为重复运行波动的对照。真实供应商缓存只观察、不宣称能够强制清空；缓存读写缺失记为 unknown。

每次调用记录排队、生成、完整处理与包含排队的耗时。每次执行段独立保存真实墙钟窗口、Runtime 启动、关闭及重开验证成本，吞吐按该段新增完成数/执行墙钟时间计算。恢复前已完成的尝试不计入新执行段吞吐；各项延迟报告 P50/P95/P99，同时给出错误和超时率。配对 Token 差值先在同一 case 内平均重复结果，再以 case 为单位进行固定种子 2,000 次 bootstrap，给出探索性 95% 区间；缺失或未知配对单独计数。

## 质量与成本口径

`run` 的每次尝试包括生成结果、审批持久化、精确 Source lineage、重开后的内容与 lineage 对照。失败、无提案、超时、未完成记录都保留在计划分母中。无提案是合法模型结果，但不计为成功生成可用 Experience。

`judge --reference-questions FILE` 使用 `blinded-experience-grounding-transfer-v4` 协议。参考问题必须先根据原始公开 source 审核并冻结；每条任务固定三问，覆盖修复方法、约束/边界和验证原则。出题不得依据生成后的 Experience、模式或评分结果。参考文件采用以下结构：

```json
{
  "protocol": "source-audited-reference-v1",
  "cases": [
    {
      "instance_id": "公开数据集中的准确 ID",
      "audit_note": "说明 source 审核依据和未执行测试的边界",
      "questions": [
        {
          "question": "问题正文",
          "expected_answer": "仅要求问题明确询问的行为",
          "evidence": [
            {"field": "patch", "start_line": 1, "end_line": 3}
          ]
        }
      ]
    }
  ]
}
```

上面仅展示一道题的形状；每个 case 必须实际提供三道题。`field` 只能为 `problem_statement`、`patch`、`test_patch`，行号从 1 起且包含两端；每题至少一段、最多 16 段。程序从原字段提取精确片段，保留换行和 diff 前缀，不接受手抄 excerpt。每题最多 600 字符、标准答案最多 2,000 字符。未知或重复 ID、越界范围、缺少计划 case、数据集 SHA 不一致以及尚未完成的生产任务，均在打开供应商前拒绝。程序验证引用位置，不能自动保证问题与答案在语义上正确；source 审核责任与方法需要另外说明，不能把 agent 审核称为独立人工专家审查。

先做不读取凭据、不调用数据库或模型的校验：

```bash
.venv/bin/python -m evaluation.performance.input_encoding judge \
  --env-file /absolute/path/.env --output /data/codex-tmp/input-encoding/main \
  --reference-questions /data/codex-tmp/input-encoding/reference-questions.json --validate-only
```

正式执行时，回答模型只看到 Experience 和问题正文，看不到原始 issue、patch、证据片段或标准答案。评分模型看到完整原始 issue、patch、test patch 三个结构化字段、引用片段、参考答案及回答；不使用嵌套 JSON 字符串投影，也不只展示选定引用，以便发现引用范围外的矛盾和编造。两者均看不到模式标签。评分沿用问题本身的要求，接受语义等价答案，不将标准答案中的额外说明变成隐藏条件；诚实的“信息不足”计为迁移未成功，不能说回答模型忽视了它从未看到的原始证据。

参考文件 SHA、解析后摘要、完整参考快照和生产结果摘要进入 judge 身份；更换问题、引用或生产记录都会拒绝在原目录续跑。每 case 仍保存 `questions/*.json`，但标记 `source=audited_reference`、`actual_network_attempts=0`，出题用量明确为零，因为没有执行模型作者调用。此前中断或失败的评分目录须另名保留，其已知成本和未知账单继续计入整个实验。

不提供参考文件时仍支持自动出题协议 v3：仅为至少有一个成功产物的 case 出题；生产失败或无提案仍保留在计划分母中，不发起其出题请求。作者只看公开原始证据，每题要求最多 300 字符的连续逐字片段，匹配失败计为未评。不能把不同出题协议的评分混为同一批结果。

盲评的 `--protection-config` 应与真实生成使用同一份部署保护配置；`--max-output-tokens` 默认 4096，同时约束实际模型请求和单次生成预算。manifest 记录保护模式、输出上限、请求/时间预算、提示词及输出 schema 摘要。完整原始证据仍可能超出保守 wire 预算；`--validate-only` 仅校验参考及覆盖，不认证请求预算。实际保护边界继续拒绝超限请求，不能为完成评分放宽产品 guard 或静默裁剪证据。失败诊断只保存异常类型、已知静态错误类别与隐私拒绝码，已捕获用量作为小计保留；失败及未完成调用的账单完整性保持未知。

“可用”阈值固定为证据支持至少 1、无矛盾 2、结果诚实 2、可操作性至少 1，每项 0–2 分。主正确率使用全部计划尝试为分母，并额外报告已评分样本的各项均分。报告区分生产失败、合法无提案、缺少/无效参考、回答失败、评分失败和仍未完成；缺少评分不等于已经证实质量错误。LLM 评分可能犯错，同模型生成和评分也有偏好风险，需抽查答案及理由。回答模型可能凭预训练知识猜中，因此这些问题只是知识保留和可用性的代理，不代表真实仓库修复成功；未预设非劣界限时，不声称“无损已证明”。

v4 每个成功产物最多一次回答与一次评分，单次模型调用最多两次请求；失败/无提案产物不调用评分模型。两个 cohort 若共有 205 份成功产物，基础规模为 410 次模型调用，格式重试另计。v3 自动出题的 24 case / 144 尝试上限另有 24 次出题调用。各阶段成本与 Experience 生产流程分别记录，再汇总整个实验的已知 Token 小计。当前 Experience 的生成、审批和精确读回不包含向量化：审批在同一事务内维护词法全文索引，不请求 embedding，也不会将其延后交给 worker。采集器仍按 operation 分开保存实际出现的 generation 和 embedding 用量。

输入、输出、总 Token、cache read/write 和 requests 仅来自现有模型用量报告。评测观察器包装公开的 `ScopedStatisticsApplication.record_model_usage`，捕获后继续调用原方法，原有落库行为保留；不注入替代生成器或模拟供应商响应。没有某个 operation 的事件表示未观测到该用量；通用汇总会给出已知小计 0，并将字段完整性保留为 unknown，不能把小计当成供应商零收费证明。该观察器覆盖当前尝试，不代表整个部署、独立后台任务或数据库索引维护没有费用。估算只存在于 `encodings`，不参与实际计费求和。没有显式价格配置时金额为 `null`。

配对差值和主表使用 `generation` 用量；若观察到 embedding 事件，其输入独立统计，不能计为生成重试。生成请求数多于一次可能来自格式修复或续写；SDK 未暴露的传输重试无法从这些计数推算。整个生产流程和评分流程分别汇总已知实际 Token，再列出合计。embedding 没有提供输出 Token 时保留未知，不填零凑成一个完整总数。当前套件不计算货币金额。

## 恢复与结果

`manifest.json` 固定数据 SHA、投影版本、源码摘要、模型、配置摘要、随机种子、重复次数与并发度。配置或源码改变时拒绝续跑，应建立新输出目录。每个尝试在发送前先保存 `running` 记录，完成后原子替换；恢复时不自动重发已有记录，以免对超时后是否计费的调用重复付费。残留 `running` 保留为未完成与用量未知，需要单独确认后另开实验。

`run` 和 `judge` 都在打开数据库或供应商之前验证私密配置身份。各自输出目录内的 `.config-identity.key` 仅当前用户可读写，用于对完整配置及有效供应商环境计算 HMAC；公开 manifest 只保留 HMAC 和掩码配置摘要，身份实现本身也进入源码摘要。数据库 URL、策略中的 `SecretStr` 或 API 凭据改变，密钥丢失、轮转或权限过宽，都会拒绝续跑。密钥不能进入分享的报告或归档。

私密身份明确支持 OpenAI、OpenAI Chat/Responses 与 Anthropic 的环境凭据、组织/项目、endpoint 和自定义请求头，并纳入相关传输环境；其他供应商会明确拒绝身份认证，尤其不能用云凭据文件路径代替实际凭据身份。仅有掩码摘要的旧 manifest 无法证明 `SecretStr` 或独立 API key 的身份，新执行入口拒绝续跑这些目录。旧记录仍可独立生成报告；已有实验的配置身份依据是外部私密配置指纹和实际执行源码快照，报告需披露这一元数据版本区别。为旧生成结果新建盲评时，盲评会保存自己的配置身份，不会修改生成 manifest。

Anthropic 仅配置 `Authorization` 头且没有静态凭据时，SDK 可能从 profile 文件发现额外的 endpoint、账号或凭据。此路径会在接受实验身份、打开服务前明确拒绝；需要配置已纳入身份的 `ANTHROPIC_API_KEY`、`ANTHROPIC_AUTH_TOKEN` 或 `X-Api-Key`。套件不尝试通过 profile 名称或文件路径认证其有效内容。

```bash
.venv/bin/python -m evaluation.performance.input_encoding report \
  --output /data/codex-tmp/input-encoding/main
```

`attempts/*.json` 保存逐次证据，`judge/{questions,answers,assessments}/` 保存全部评分调用，`report.json` 保存分组及配对差值，`report.md` 提供简表。比较算法变化时保留旧版离线和真实结果，不能在同一批次中替换算法后只公布更好的部分。

离线算法的 `applied` 只是编码资格；最终是否选择编码输入以真实调用的 `encodings.reason` 为准：Prompt 不兼容、编码收益不足、还原失败或候选表示不满足隐私 profile 时，准备阶段保留原始输入。最终 wire 预算检查会拒绝超限请求，不会通过重排或再次压缩来放行。安全异常额外保存 `phase`、`decision` 和 `reason_codes`，不保存原始异常消息。

安全拦截、跨 Scope 隔离、拒绝后的零转发、恶意证据及超大输入属于单独的受控回归，不混入公开数据集质量得分。公共数据集负载与合成重复日志压力也应分别标注。

## 编码表示的固定语义探针

`semantic-probe` 直接让模型阅读生产 codec 选出的表示，再回答固定问题。它有 7 组纯合成证据、29 个问题：
主项目目录与临时目录的区别、字面占位标记与未知原值、Source 身份和 Scope 归属、重复次数与顺序、对象与 JSON
字符串的类型，以及换行和空白。另有一个短输入对照，明确不能应用编码。`{{USERNAME_abcdefgh}}`、
`{{TOKEN_abcdefgh}}` 和 `${API_KEY}` 都是输入中原本存在的合成字面标记，不是这套 codec 提供了脱敏或变量替换功能。

预期答案是仓库内固定的字面量，不进入模型输入，也不从 decoder 的输出生成。模型通过正式生成适配器接收选中的原始
或编码文本；评分器只比较模型答案与固定预期，区分 `true`、`1`、`1.0`、字符串和对象，并检查出现次序与精确字符。
v2 还要求实际待答的 JSON 字符串使用 `j` 加共享引用、换行字段使用 `s` 加共享引用；只压缩无关填充不算覆盖。

先检查题集、渲染与编码选择，不读取私密配置，也不调用模型：

```bash
.venv/bin/python -m evaluation.performance.input_encoding semantic-probe \
  --dry-run --output /data/codex-tmp/input-encoding/semantic-plan
```

`plan.json` 可检查每次实际选择的 prompt、编码报告和待答字段的编码覆盖。dry-run 的 `semantic_evaluation` 始终是
`not_run`，不产生语义通过结论。真实调用必须显式提供模型配置：

```bash
.venv/bin/python -m evaluation.performance.input_encoding semantic-probe \
  --env-file /absolute/path/.env --schema both \
  --output /data/codex-tmp/input-encoding/semantic-model-run
```

`--schema v1` 或 `v2` 可单独运行；默认 `both`。每种 schema 对每组题分别运行 `off`、`shadow`、`active`，默认共
42 次尝试、174 次逐题评分。顺序按固定种子交错，每次最多 1 个模型请求，默认输出上限为 2,048 Token、超时 90 秒；
可以用 `--max-output-tokens`、`--timeout` 调整，或用 `--protection-config` 配置现有传输保护。未提供工具，也不连接
数据库。关于搜索原值或索取秘密的问题只测模型给出的选择，不证明真实工具执行的安全性。

`report.json` 保留各 schema/mode 的固定题组和问题分母、未运行/错误状态、正确数、编码实际应用数与 active 回退数。
缺少结果或实际编码覆盖不足时，`all_probe_cases_passed` 为 false；active 回退即使答对，也不能计为编码理解通过。
短输入对照单独标为无需编码。该字段只表示这组固定题目通过，不表示模型在任意输入上都能可靠理解编码。
用量缺失不会补零；失败或未完成尝试存在时总量保持未知，已知小计单列。Token 用量未知与答案正确性分别报告。

manifest 固定题集与预期、codec、探针、Runtime/适配器源码、提示词、模型配置和预算身份。已有尝试不会自动重放；恢复
时校验尝试 ID 与 prompt 摘要，并根据保存的实际输出重新评分。更改源码、题目或配置应使用新的输出目录。

合成证据有意重复，以覆盖真正应用编码的情况，不代表自然输入分布或平均压缩收益。这个探针只检验所选 codec/model
组合在固定问题上的表现，不能替代 `atomic_memory.extract`、`atomic_memory.reconcile`、`experience.generate`、
`experience.incubate` 四个操作的真实 Runtime 质量验收。现有 Experience 的生成、持久化、盲评与成本评测仍分别执行。
仓库中的受控模型测试只验证接线、评分与恢复逻辑，不是实际模型语义验收结果。
