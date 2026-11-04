---
title: 捕获覆盖声明契约
description: Hermes 与 Gateway Content Source 投影的版本化覆盖声明。
---

# 捕获覆盖声明契约

Hermes 和 Gateway 在新 Content Source 的 metadata 中增加
`powercontext_capture_coverage`。这个有界声明记录生成受支持投影时实际观察到的容量损失，
不表示完整原始会话。

本文定义 [issue #2437](https://github.com/oceanbase/powercontext/issues/2437) 对应的
`powercontext.capture-coverage.v1`。共享合成用例位于
`tests/integration_conformance/capture_coverage_cases.json`；Python 和 Rust 使用同一份文件，
不共享运行时，也不引入插件框架。

## 声明格式

```json
{
  "powercontext_capture_coverage": {
    "schema": "powercontext.capture-coverage.v1",
    "projection": "hermes-turn-text.v1",
    "unit": "user_assistant_text_pair",
    "status": "complete",
    "reasons": []
  }
}
```

声明恰好包含这五个字段。对象自身不得超过 512 UTF-8 bytes：按不做 ASCII 转义的紧凑 JSON
计算，不包含外层 metadata 键。v1 合法字段值均为固定枚举，因此契约本身保证该上限。
对象键顺序不影响含义；reasons 数组顺序属于契约。其他 metadata 必须保持原样，包括不透明的
prefix extraction 扩展。不得加入正文、路径、message ID 列表、prompt、模型配置或凭证。

| 字段 | 契约 |
| --- | --- |
| `schema` | 固定为 `powercontext.capture-coverage.v1`。 |
| `projection` | 下表定义的三个 profile 之一。 |
| `unit` | 与 profile 精确配对的投影单元。 |
| `status` | `complete`、`partial` 或 `unknown`。 |
| `reasons` | 受支持字符串，去重并按字典序升序排列。 |

支持的 reason 为 `character_limit`、`field_byte_limit`、`transcript_byte_limit`
和 `unmeasured_projection`。

| 实际观察到的事实 | status | reasons |
| --- | --- | --- |
| 受支持投影可测量，没有额外容量损失。 | `complete` | 空数组。 |
| 至少发生一种容量损失。 | `partial` | 所有已观察到的容量损失原因。 |
| 无法测量覆盖情况，且没有已知容量损失。 | `unknown` | `unmeasured_projection`。 |
| 已知损失与不可测量部分同时存在。 | `partial` | 已知损失原因，加上 `unmeasured_projection`。 |

partial 必须包含实际容量损失原因，不能仅包含 `unmeasured_projection`。unknown 不能掩盖已知损失。
metadata 缺失、没有该键、schema 或 profile 不支持、unit 不匹配、字段错误或类型非法、存在额外字段、
reason 重复或乱序、status 与 reasons 矛盾，都解释为 unknown。解释过程不改写已存储 payload。

## 受支持投影

| profile | unit | 投影边界 |
| --- | --- | --- |
| `hermes-turn-text.v1` | `user_assistant_text_pair` | 当前规范化后的 user/assistant 文本对，包含既有角色标签和分隔符。 |
| `hermes-precompress-text.v1` | `new_user_assistant_window` | 宿主提供、经现有过滤及重叠去重后，本次待捕获的新 user/assistant 条目窗口。 |
| `gateway-turn-text.v1` | `gateway_turn_projection` | 当前受支持的 Chat/Responses 规范投影，从最后一条 user 到 terminal assistant。 |

### Hermes completed turn

覆盖判断使用裁剪前的完整拼接字符串，包含角色标签及分隔符，并沿用 50,000 字符上限。
恰好达到上限为 complete；实际裁剪为 partial/character_limit。这里计算 Python 字符数，
不是 UTF-8 字节数。

正文、原有 source ID 算法、角色选择、secret handling 和队列行为均保持不变。
`messages` 参数不会把该 profile 扩展成完整工具历史。空文本对不创建 Source。
类似截断 marker 的正文始终只是普通数据。

### Hermes pre-compress

宿主提供 `evidence_messages` 时优先使用它。继续沿用现有 user/assistant 过滤、secret redaction、
fingerprint 和 overlap 处理。投影单元是本次请求捕获的完整新窗口，不是最后能装下的前缀。

现有 30,000 字符上限只接纳完整条目。全部新条目可容纳时为 complete；optional 模式捕获非空前缀
时为 partial/character_limit。第一条就无法容纳时不创建空 Source。required checkpoint 无法容纳完整
窗口时，必须在发送部分 Source 或推进已捕获快照之前拒绝。

Scope 固定、重叠身份和 checkpoint 状态转换保持现有语义。capture 已持久化之后，后续 extraction
失败不否定 checkpoint。

### Gateway normalized turn

继续保留既有可见文本、工具调用和工具输出投影。注入的 prepare、opaque reasoning、传输 ID 及不支持
的媒体仍在 profile 之外；排除它们本身不代表该投影 partial。

实际执行现有 8 KiB 字段裁剪时记录 `field_byte_limit`；按现有 64 KiB transcript 策略实际删除步骤时
记录 `transcript_byte_limit`。同时发生则同时保留，即使被裁剪的字段后来随步骤一起省略，也不能丢失
已经观察到的事实。UTF-8 边界、正文 JSON、`omitted_steps`、截断 marker 和终止响应判断保持不变。
coverage 与正文来自同一次投影计算，不二次解析序列化 transcript，也不使用全局状态。

已接受的 Responses 历史条目出现未知 type 时，现有投影会跳过该条目，因此声明为不可测量。
历史消息不恰好包含一个规范条目时也声明为不可测量；正文继续沿用既有的首条目投影。
未知终止响应、仍需客户端执行工具的响应、无法解析的响应继续不生成 transcript，也不创建 Source。
正文中的伪截断 marker 不改变覆盖声明。

Responses 调用还要求 type 为已知调用类型，并且既有投影字段 `arguments`、`input`、`action`、
`code`、`query` 或 `queries` 中存在非 null 载荷。仅因 `_call` 后缀被接受的类型，例如
`apply_patch_call` 或未来调用类型，即使包含上述字段也声明为不可测量。已知调用缺少可投影载荷时
同样不可测量；显式空字符串仍可测量。该规则同时适用于历史调用和终止响应中的 hosted 调用。
正文渲染和终止判断保持不变；不可测量调用记录 `unmeasured_projection`，并保留同时发生的已知容量损失。

Responses 历史输出仅在受支持的 function、custom、computer 或 shell 输出类型中存在非 null 的
`output` 时可测量；空字符串仍可测量。MCP 审批响应和未知 `*_call_output` 类型即使包含
`output`，也记录 `unmeasured_projection`，因为其他载荷字段没有进入投影。受支持类型缺失
`output` 或其值为 null 时同样不可测量。正文渲染和终止判断保持不变。

Chat 工具调用的 function name、arguments 均为字符串，且 type 为 `function` 或缺省时，才视为
可测量；缺省 type 沿用既有 legacy 格式。其他调用类型或无法投影的 function 字段记录
`unmeasured_projection`，不改变正文渲染。存在已知容量损失时仍为 partial，同时保留损失和不可测量原因。

## 身份、重放与升级

新捕获必须同时提交正文和声明。同版本相同重试保持 source ID、正文、声明及其他 metadata 一致，
不新增 journal position。coverage 不参与 source ID 算法。SourceRepository 继续执行完整 payload
冲突检查。

Hermes pre-compress 在发送前，按当前 provider Scope/stream 内的 Source 身份固定首次尝试的 coverage
reasons。若应答丢失，宿主随后提供更短或更长的窗口，只要捕获前缀的身份相同，重放就使用首次声明。
当前窗口能否满足 required checkpoint 仍独立判断；重放成功不会把历史 partial 声明改成 complete。

该进程内映射只为每个尝试过的身份保存固定 reason 元组，不缓存正文。内存随当前 Scope/stream 内的
不同尝试身份数增长，初始化、Scope 切换及 stream reset/rewind 时清空。它不是持久化恢复日志：provider
重启后，不会自动协调不同大小的宿主窗口与先前声明。正文及其他 metadata 的真实冲突始终保留。

仅在 Hermes capture 操作中，409 可以触发最多一次 legacy 重试：使用相同 Scope、source ID、正文及
其他 metadata，只移除本次新增 coverage 键。只有 Server 接受完全相同的 legacy payload，重试才算
成功。再次失败保留原冲突；首次 401、403 和传输失败不触发该路径。普通 turn 的失败诊断和 required
checkpoint 的失败行为仍由各自边界负责。

legacy 重放成功后，旧 Source 仍然没有声明，解释为 unknown。不回填历史、不换 ID、不增加网络查询，
也不修改通用 HTTP client 的重试策略。Gateway 继续沿用 409 first-write-wins，不能因为重复提交就声称
新声明已经持久化。

## 持久化、证据与信任

现有 Content Source adapter 和 TextEvidence 投影保留 metadata。重新打开 SQLite 后，通过现有
SourceRepository 完整读回可以取得原始声明及其他 metadata。现有 HTTP `get_source` 对 Content
Source 只暴露正文和身份，**不返回 metadata**；本功能不增加 HTTP 响应字段。E2E 通过真实 Server
进程重启验证 HTTP 正文和身份，再通过现有 repository 读回验证完整 metadata。

capture coverage、`EvidenceManifest.incomplete`、隐私 admission 的 `coverage.complete`、
authority/verification 和 root independence 是不同事实。caller 提供的声明不证明来源，也不授予
权限。Core 不根据该键改变 generation eligibility、authority、trust policy、Memory 内容或召回。

声明始终是不可信证据数据，不转换成系统指令。它保存在 Source metadata、进入提取输入，并不承诺
经过模型摘要后还能确定性传播到 Memory 或 PreparedContext。本功能不提升任何宿主的生命周期能力。

## 输入成本与验证

同一声明可能同时出现在证据内容和 provenance 中。应测量完整输入，不能把声明对象的字节上限当成
模型输入预算。

共享 fixture 的 `budget_sample` 使用 `hermes-turn-text.v1`、内置 coding 提取指令、注入的 recording
extractor，不配置自定义 input renderer，estimator 为 `character:weighted`、版本 `1`：

| 测量项 | 无声明 | 有声明 | 增量 |
| --- | ---: | ---: | ---: |
| 声明对象紧凑 UTF-8 bytes | 0 | 147 | 147 |
| 序列化 `AtomicMemoryExtractionInput` UTF-8 bytes | 608 | 968 | 360 |
| 指令 + 换行 + 序列化输入的 estimated tokens | 1,238 | 1,328 | 90 |

这些是合成输入的测量结果，不是真实模型 token 用量。指令、渲染方式、estimator 和输入内容都会影响
总量。完整输入预算检查保持有效：单 Source 超限时拒绝且不消费 cursor；多 Source 窗口超限时继续沿用
现有持久化缩窗及重试行为。两条路径都不能丢弃声明或放宽预算。

验证命令：

```bash
uv run pytest tests/integration_conformance tests/integrations/test_hermes_provider.py -q
uv run pytest tests/e2e/test_capture_coverage.py -q
cargo test --manifest-path gateway/Cargo.toml -p pc-gateway-core
cargo clippy --manifest-path gateway/Cargo.toml -p pc-gateway-core --all-targets -- -D warnings
```

E2E 覆盖真实本地 HTTP、独立 Server 进程及 SQLite、精确重放、冲突、Scope 隔离、TextEvidence 和
AtomicMemoryEvidence。模型生成使用 recording extractor；直接调用 Hermes provider 验证的是 adapter
行为，不代表完成原生 Hermes 宿主验收。Gateway 捕获通过 executor 和受控协作者验证。
