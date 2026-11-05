# OceanBase 原生全文覆盖条件：生产索引方法对照

本目录验证 PowerContext 的自动原生全文路径，比较同一 OceanBase 实例、同一份数据上的两个生产实现。测量入口是 `OceanBaseAtomicMemoryIndex.search()`、`enumerate_related_with_heads()` 和 `probe_recoverable()`，不以手写近似 SQL 的耗时替代真实方法调用。

- `baseline`：从完整 Git 提交 `c4b9b540a6daa6514be83f1696e0fccf18c5c147` 读取原 OceanBase 索引模块，加载其类与方法，保留逐词 LOCATE 条件。
- `automatic`：直接导入当前工作树的 OceanBase 索引类，由生产能力检测自动选择路径；runner 不改写 SQL，也不设置私有能力标志。
- 两个类导入当前共享类型、Analyzer 与表定义。`experiment.json` 同时保存冻结模块、当前源码和语料的 SHA-256，避免将它误称为两个完全独立的安装环境。

## 运行

从仓库根目录执行。先准备依赖，再使用专用测试实例的连接文件；连接文件不属于仓库。新测试必须使用新的输出目录，runner 会创建随机命名的 `pc_fts_bench_*` 专用数据库。它没有接受业务库名称的参数。

```bash
# 仅准备夹具：不读取连接文件，不连接数据库。
python scripts/benchmark_oceanbase_fts_native.py \
  --prepare-only --output /path/to/local-check

# 首先在一个实例上验证小夹具。
python scripts/benchmark_oceanbase_fts_native.py \
  --connection-file /path/to/private-connections.json \
  --versions v4601 --semantic-only \
  --output /path/to/new-semantic-run

# 两版顺序执行，逐步增加数据规模。
python scripts/benchmark_oceanbase_fts_native.py \
  --connection-file /path/to/private-connections.json \
  --versions v4601,v501 --sizes 100,1000,10000 --cycles 2 \
  --output /path/to/new-scale-run
```

连接文件格式如下。标签指向用户提供的相应实例；实际服务端版本另行保存，不能仅用标签确认版本。

```json
{
  "host": "test-host",
  "username": "test-user",
  "password": "supply-locally",
  "ports": {"v4601": 2881, "v501": 2882}
}
```

默认 corpus 为本目录的 [corpus.json](corpus.json)，包含既有的 24 条 LLM 合成记忆和四个普通问题。runner 不调用 LLM 或 embedding。扩展到更多行重复这些模板，不等于新增相同数量的独立真实记忆。`--datasets corpus` 或 `--datasets controlled` 可仅执行一种规模数据集；小语义夹具始终先执行。

## 数据与验证范围

投影通过实际 `AtomicMemoryProjectionPublisher.prepare()` 与 `index.replace_many()` 构造。权威 body/head 表使用当前 schema 写入可验证的固定数据，再由完整枚举的实际连接查询读回。这里验证索引边界；它不等于经过 HTTP、完整 Artifact 服务或整个公开 runtime 的验收。

小夹具共 147 条记录、14 个问题，每个问题同时测试有界搜索和无上限枚举：

| 场景 | 要保护的结果 |
| --- | --- |
| 1、2、8、17、128、485 个不同词 | 短问题命中 1 词；其他问题满足完整词覆盖阈值 |
| 正文重复、词的子串 | 重复不增加不同命中词数，子串不能替代完整词 |
| 重复查询词 | 输入去重后计算覆盖条件 |
| 64 条共同命中记录、搜索限制 3 条 | 搜索有界，完整枚举返回全部 64 条 |
| 40 条不够阈值的记录与 4 条合格记录 | 覆盖准入在 LIMIT 之前 |
| 相同 artifact ID、大小写不同及带后缀的 Scope | 仅返回目标 Scope 的内容和权威 head |
| kind 与 tag 同时过滤 | 过滤参与准入，搜索与枚举一致 |
| 中文、标识符、Unicode | 保留当前 Analyzer 的实际处理结果 |
| revision、state_version、governance_generation、body | 命中身份与枚举附带的权威信息相符 |

普通语料按 100 → 1,000 → 10,000 总行数递增，目标 Scope 通常为 80%。受控数据使用相同规模，正文包含不同词数、重复词频和长度；正式性能查询使用 128 和 485 词。每个阶段执行 ANALYZE 并读回投影数据哈希。受控 100、1,000 行还验证 `q0000` 的完整枚举；10,000 行不额外重复全量枚举。

受控数据的每个规模还分别测量两个 485 词 recovery probe：降低覆盖率至 `AdmissionFloor(lexical_coverage=0.20)` 后有新增合格记录的查询，以及 485 个正文未出现词的查询。独立 oracle 计算较低阈值合格集合减去当前合格集合，检查返回布尔值和两实现的一致性。正负 probe 分开统计，避免仅观察容易提前返回的正例。仅在运行时调用方传入 `recovery_admission` 时使用该探针；不意味着每次搜索都执行它。

每版各组先预热一次，再执行 `baseline → automatic → automatic → baseline`。默认两个周期，即每个实现四次正式测量。小夹具和附加枚举用于语义核对，其耗时不混入规模搜索中位数。

## 计时与正确性

计时覆盖实际索引方法调用及其数据库往返、取数、生产代码内部的结果处理。夹具构造、写入、ANALYZE、runner 的离线 oracle 和证据序列化不计入该时长。能力检测随生产初始化预热；不是冷启动延迟或并发容量测试。

SQLAlchemy 事件只观察执行的 SQL 结构，记录哈希、字符数、绑定参数数量、是否包含原生 MATCH、LOCATE 和 LIMIT。它不改写语句、不保存连接 URL 或参数值。新版 automatic 必须实际走原生查询，baseline 必须实际包含 LOCATE，不能把能力误判后的回退当作原生性能。

独立准入检查依据已准备正文的词集合验证：返回 ID 合格且不重复、数量正确、能完整返回时集合相等；同时检查内容身份和枚举 heads。它没有另行实现 BM25，候选超过 LIMIT 时不独立证明相关性 TopK。

对照要求有序 ID 完全相同。raw score 同时记录精确相等与浮点近似相等，近似容差为 `rel_tol=1e-12`、`abs_tol=1e-12`，并报告最大绝对差。容差只用于数值，不放宽排序或集合。`ChannelScore` 仍须符合 `metric="oceanbase_match"`、`higher_is_better=True` 的原合同。基线无成功结果的长查询不能声称排序等价。

## 失败与清理

被测方法的服务端限制为 `ob_query_timeout=10000000`，客户端调用上限为 60 秒。建表、写入、清空夹具、ANALYZE 和数据核对使用独立的 180 秒维护限制，均不计入查询时长；维护阶段无论成功与否都会尝试恢复并读回 10 秒限制，每组被测方法调用前再次读回核验。维护日志记录阶段、操作、表名（适用时）和恢复结果，维护失败与被测调用失败分开报告。

失败记录保留异常类型和数值错误码，不输出异常原文或凭据。方法调用失败后执行 rollback、invalidate、close，再重连并读回 10 秒限制。只检查本次随机专用库及已记录会话 ID；连续两次没有活跃或 `SESSION_KILLED` 会话才继续。180 秒内仍有残留则停止整次运行。

小夹具或完整枚举执行错误、结果错误会中止。性能阶段某条查询的某个实现失败后，该实现同查询的后续尝试和更大规模槽位标为 `skipped`；另一实现可在会话清空后继续。预热失败可能导致四个正式槽位均未执行，不能把它写成“四次正式失败”。成功中位数不包含失败，`skipped` 不进入实际尝试分母。

退出码及最终 `outcome.json`：

| 退出码 | 含义 |
| ---: | --- |
| 0 | 已执行计划完成，结果验证通过，没有执行失败 |
| 1 | 配置、语义、清空检查或实验执行中止 |
| 2 | 其他步骤成功，但仍有待清理数据库 |
| 3 | 测量完成，存在结果对照差异 |
| 4 | 测量完成，性能调用有失败；保留失败和跳过记录 |

创建数据库、取得连接 ID 时立即持久化 `journal.json`。正常结束会清理本次专用库并读回确认不存在；有活跃残留时保留库而不是强行删除。中断后的限定清理可用：

```bash
python scripts/benchmark_oceanbase_fts_native.py \
  --connection-file /path/to/private-connections.json \
  --cleanup-only /path/to/run/journal.json --output /path/to/run
```

清理会校验目标指纹、随机运行 ID 和允许的数据库名称。退出码 3/4 可能同时存在清理未完成，最终清理状态以 `cleanup.jsonl` 与 journal 为准，不能仅依据退出码判断所有资源已释放。

## 输出

以下文件生成到 `--output` 指定的目录，不随源码提交。建议使用仓库外的输出目录保存原始日志。

- `experiment.json`、`metadata-*.json`：源码、语料、版本、查询超时及 schema。
- `runs.jsonl`：每次实际方法调用、原始分数、SQL 结构观察、oracle 和失败/跳过状态。
- `comparisons.jsonl`、`summary.json`：结果对照与分阶段、预热/正式分开的成功延迟。
- `population.jsonl`、`maintenance.jsonl`、`query-timeout-checks.jsonl`：装载规模、投影数据指纹、维护操作及查询超时恢复/核验。
- `recovery.jsonl`、`drain.jsonl`、`cleanup.jsonl`、`journal.json`：错误后的恢复、残留与限定清理。
- `outcome.json`：完成状态、退出码、方法调用失败、实验级错误及不同结果计数。

本轮测量结果整理在 [RESULTS.md](RESULTS.md)。
