# OceanBase 原生全文覆盖：生产方法验证结果

测试日期：2026-11-05。分支：`codex/oceanbase-native-minimum-should-match`。基线提交：`c4b9b540a6daa6514be83f1696e0fccf18c5c147`。

实机数据采集于同步上游 `20f64d72` 之前。同步后，被记录的全文检索实现、共享索引/schema/Analyzer 文件及语料保持逐字节一致；实机 runtime 用例未在同步后的完整 checkout 上重跑，不能将其视为对新 HEAD 全部上游变更的验收。

本次直接调用生产 `OceanBaseAtomicMemoryIndex.search()`、`probe_recoverable()` 和 `enumerate_related_with_heads()`。基线从冻结提交加载原 OceanBase 类，自动路径使用当前实现；两者在同一个实例、同一 schema 和同一份数据上交替运行。结果不以手写 SQL 代替生产方法，也不将跨版本升级效果算作这次应用改写的收益。

**在 10,000 行、485 词的受控搜索中，两版基线均在预热时超时；自动原生路径四次正式调用全部成功，中位数分别为 1,256.2 ms 和 1,019.8 ms。** 但恢复探针存在明确取舍：容易找到新增候选的正例从约 66–73 ms 变为 2.4–2.9 秒；无新增候选的负例大幅改善。不能概括为所有路径都更快。

## 环境与计量口径

| 服务端版本 | 租户 CPU | 租户内存 | 默认查询超时 | optimizer_features_enable | 本轮用途 |
| --- | ---: | ---: | ---: | --- | --- |
| 4.3.5.6 | 3 | 5 GiB | 10 秒 | 4.3.5.6 | 兼容路径小规模实机验证 |
| 4.6.0.1 | 3 | 5 GiB | 10 秒 | 4.6.0.0 | 生产方法对照与实机用例 |
| 5.0.1.0 | 3 | 5 GiB | 10 秒 | 4.6.1.0 | 生产方法对照与实机用例 |

实例使用 MySQL 模式，计划缓存开启，位于同一台用户提供的主机；实验串行执行。配额相同不代表独享资源，本轮没有并发容量、服务端 CPU、算子或完整审计耗时归因。原始配置见 [preflight.json](evidence/20261105/preflight.json)。

表格中的延迟单位为毫秒，均为每个实现四次成功正式调用的中位数；错误和未执行槽位另列。每组先各预热一次，再执行两轮 `baseline → automatic → automatic → baseline`。能力探测已预热，计时包含方法内的数据库往返、取数和结果处理，不包含装载、ANALYZE、runner oracle 或落盘。表中的搜索与恢复探针分别计时，没有将两者相加冒充实测端到端请求。

所有被测方法的 `ob_query_timeout` 为 `10000000` 微秒，客户端方法截止时间为 60 秒。`full` 的初始化元数据记录该设置，维护阶段也使用 10 秒；`controlled` 和 `corpus-v501` 的维护操作使用 180 秒，在 `finally` 恢复、读回 10 秒，并在每组方法调用前再次核验。服务端参数不等于 Python 方法墙钟的严格上限，成功调用仍可能超过 10 秒。维护阶段的 180 秒不用于被测检索。

普通问题使用 [corpus.json](corpus.json) 中既有的 24 条 LLM 合成记忆和四个问题，问题分析后分别为 14、16、14、17 个不同词。扩容重复这些模板，10,000 行不等于 10,000 条独立真实语料；本轮没有新的 LLM 调用。受控数据使用 128/485 词、阈值边界、重复和子串夹具，最长正文为 5,867 UTF-8 字节，经过当前生产 `prepare()` 的大小校验。两类数据规模均为 100、1,000、10,000 总行数，目标 Scope 占 80%。

## 普通问题搜索

每格为四个问题各自正式中位数的最小值–最大值，不是单条查询的波动范围。

| 版本 | 实现 | 100 行 | 1,000 行 | 10,000 行 |
| --- | --- | ---: | ---: | ---: |
| 4.6.0.1 | LOCATE | 6.3–9.3 | 40.5–44.6 | 361.5–408.2 |
| 4.6.0.1 | 原生 | 2.3–5.4 | 7.0–10.3 | 23.5–40.8 |
| 5.0.1.0 | LOCATE | 9.3–11.2 | 39.5–42.9 | 364.3–418.8 |
| 5.0.1.0 | 原生 | 4.8–6.6 | 7.0–8.7 | 26.2–40.4 |

两版合计 192 次正式调用和 48 次预热均成功，24 个同版本、同规模、同问题的对照组全部通过。10,000 行逐问题配对后的中位数加速分别为 10.0–15.4 倍和 10.4–15.2 倍。数据来自 [4.6 summary](evidence/20261105/full/summary.json) 与 [5.0 summary](evidence/20261105/corpus-v501/summary.json)。

## 长问题搜索

`—` 表示没有成功正式样本；不能换算成加速倍数。

| 版本 | 词数 | 100 行：LOCATE / 原生 | 1,000 行：LOCATE / 原生 | 10,000 行：LOCATE / 原生 |
| --- | ---: | ---: | ---: | ---: |
| 4.6.0.1 | 128 | 44.9 / 10.1 | 381.2 / 71.6 | 8,307.3 / 526.8 |
| 4.6.0.1 | 485 | 179.0 / 30.1 | 1,305.5 / 104.4 | — / 1,256.2 |
| 5.0.1.0 | 128 | 42.6 / 9.9 | 380.2 / 69.5 | 8,813.1 / 482.5 |
| 5.0.1.0 | 485 | 181.6 / 31.2 | 1,403.3 / 160.9 | — / 1,019.8 |

10,000 行的两条 485 词基线各有一次预热 `4012`，墙钟分别为 10,087.3 ms 和 10,069.5 ms；后续四个正式槽位各自跳过。其余长搜索正式调用均成功。数据见 [controlled/summary.json](evidence/20261105/controlled/summary.json) 和 [runs.jsonl](evidence/20261105/controlled/runs.jsonl)。

## 恢复探针

以下均为 485 词。正例返回 `true`，负例返回 `false`；每个格子的两个数字仍为 LOCATE / 原生的成功正式中位数。

| 版本 | 场景 | 100 行 | 1,000 行 | 10,000 行 |
| --- | --- | ---: | ---: | ---: |
| 4.6.0.1 | 有新增候选 | 54.9 / 58.6 | 56.0 / 214.5 | 72.9 / 2,883.0 |
| 4.6.0.1 | 无新增候选 | 148.3 / 39.1 | 1,267.8 / 53.8 | 11,588.4 / 103.2 |
| 5.0.1.0 | 有新增候选 | 62.4 / 63.1 | 68.8 / 323.5 | 66.4 / 2,408.7 |
| 5.0.1.0 | 无新增候选 | 177.4 / 47.4 | 1,274.2 / 51.4 | — / 96.5 |

5.0.1.0 的 10,000 行负例基线预热在 10,081.4 ms 返回 `4012`，四个正式槽位跳过。4.6.0.1 同组的预热和四次正式调用均成功，但方法墙钟全部超过 10 秒，正式范围为 11,173.4–11,963.9 ms。记录中的服务端限制仍为 10 秒；本轮没有拆分服务端执行、协议往返和客户端处理，不把该差异解释成已定位的超时缺陷。

正例探针在 10,000 行明显变慢。基线的 `LIMIT 1` 有提前结束机会，原生实现保留当前阈值与较低阈值的两组完整匹配，再判断差集；这是查询结构上的取舍，没有算子剖析支持精确成本归因。正负例展示不同候选分布，没有测量线上两者的比例。search 的收益不能替代该探针的独立性能结论。

## SQL 规模

以下来自实际方法执行时的只读 SQLAlchemy 事件观察。字符数是驱动收到的占位符 SQL 文本，参数数是本次传给驱动的参数位置数，包含重复绑定位置；不是驱动替换参数后发往服务器的完整文本长度。

| 方法 | 查询词数 | 实现 | SQL 字符数 | 参数位置数 | LOCATE 数 |
| --- | ---: | --- | ---: | ---: | ---: |
| search | 128 | LOCATE | 12,630 | 134 | 128 |
| search | 128 | 原生 | 271 | 4 | 0 |
| search | 485 | LOCATE | 46,902 | 491 | 485 |
| search | 485 | 原生 | 271 | 4 | 0 |
| recovery | 485 | LOCATE | 93,275 | 973 | 970 |
| recovery | 485 | 原生 | 623 | 6 | 0 |

原生 search 的 SQL 表达式和绑定位置不随词数增长，仍通过参数传入完整词表和覆盖阈值。参数内容长度、索引查找与候选处理的工作量仍随词数和数据变化；固定 SQL 不能解释为固定执行复杂度或对任何输入都保证 10 秒完成。本轮 automatic 的路径断言只观察真实方法调用，不将初始化能力探测混入原生检索计数。

## 正确性边界

准入 oracle 使用当前 Analyzer 生成的查询词和已准备正文的词集合，核对集合交集、阈值、Scope、kind/tag、结果数量和唯一性。它与生产共享 Analyzer 和阈值函数，没有独立实现 Analyzer 或 BM25。搜索双方成功时要求有序 ID 完全相同；raw score 记录精确相等与 `rel_tol=1e-12, abs_tol=1e-12` 下的一致性，数值容差不放宽排序或集合。

候选超过 LIMIT 时，独立 oracle 只能确认返回项合格、数量正确；双方成功的对照另外验证其观察到的排序一致。基线超时的组无法据此声称相关性 TopK 或分数与基线等价。

恢复探针调用真实 `probe_recoverable()`，将覆盖率从默认 0.25 降至 `AdmissionFloor(lexical_coverage=0.20)`。正例 oracle 的新增合格数量在三个规模分别为 15、195、1,995；负例的 485 个词均不在正文中，新增数量为 0。只有运行时调用方传入 `recovery_admission` 时才触发该探针，不能将探针成本算成所有搜索必有的步骤。

小夹具共 147 条记录、14 个问题，每版每问题分别调用搜索和完整枚举，覆盖 1/2 词、阈值边界、重复查询词、正文重复、子串、中文/标识符/Unicode、过滤在 LIMIT 之前、Scope 隔离、kind/tag 过滤，以及正文、revision、state_version 和权威 head 一致性。完整枚举另在受控 100 和 1,000 行验证超过 30 条的返回集合；没有额外跑 10,000 行全量枚举。

最终语义结论只取 controlled 中的两版：**56/56 语义对照组通过，224 次方法调用全部有效**。附加四组完整枚举也全部通过，每版在 100/1,000 行分别完整返回 72/792 条记录及权威 heads。重复出现在其他运行中的小夹具计入总调用分母，不重复计算新增覆盖场景。证据见 [controlled/comparisons.jsonl](evidence/20261105/controlled/comparisons.jsonl)。

三次运行中，能对照的全部 150 组搜索/枚举结果，其有序 ID 均完全相同；其中 98 组 raw score 精确相等，52 组存在微小浮点差，最大绝对差为 `6.750155989720952e-14`，均在既定容差内。另有 11 组可对照的恢复探针布尔值全部相同。上述 161 组包含重复的小夹具执行；按性能组单独计数为 45 组通过、3 组因基线预热失败无法对照，无已观察到的结果不一致。

## 执行分母

只统计三个正式运行目录，开发用 semantic-smoke 和实机 pytest 不混入下表。skipped 是保留在证据中的未执行槽位，不进入实际尝试分母。

| 运行目录 | 记录槽位 | 实际方法调用 | 成功 | 方法错误 | skipped | 实验级维护错误 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| full | 232 | 232 | 232 | 0 | 0 | 1 |
| controlled | 480 | 468 | 465 | 3 | 12 | 0 |
| corpus-v501 | 232 | 232 | 232 | 0 | 0 | 0 |
| 合计 | 944 | 932 | 929 | 3 | 12 | 1 |

| 调用类别 | 成功 | 错误 | skipped |
| --- | ---: | ---: | ---: |
| 小语义夹具（含重复执行） | 448 | 0 | 0 |
| 附加完整枚举 | 16 | 0 | 0 |
| search 预热 | 70 | 2 | 0 |
| search 正式 | 280 | 0 | 8 |
| recovery 预热 | 23 | 1 | 0 |
| recovery 正式 | 92 | 0 | 4 |

实际正式性能调用 372 次全部成功，另有 12 个正式槽位因对应预热已失败而未执行。不能据此写成“原计划的 384 次正式性能调用全过”。929 次成功调用全部通过路径与结果 oracle，0 次 invalid。三次运行的投影数据读回哈希均与准备数据相同。

## 实机集成验证

规模 runner 使用真实索引方法和生产投影准备，但 body/head 由夹具直接写入当前表结构；它本身不是 HTTP 或完整 runtime 验收。另有以下真实 OceanBase 用例验证生产连接与上层调用：

| 版本 | 覆盖与混合检索用例 | 1,000 词现有用例 | 结果 |
| --- | --- | --- | --- |
| 4.3.5.6 | 完整词/去重/过滤/完整枚举/恢复；混合通道与无效行检查 | 未执行 | 2/2 通过，使用兼容路径 |
| 4.6.0.1 | 同上 | runtime text/hybrid 搜索与恢复各 1 项，related 索引枚举 1 项 | 5/5 通过 |
| 5.0.1.0 | 同上 | 同上 | 5/5 通过 |

1,000 词搜索用例通过 `open_builtin_contexts()` 和 `records.create_atomic_memories()` 写入，再调用公开 runtime 搜索，核对默认阈值、较低阈值、LIMIT 和 `recoverable`；related 用例从相同 runtime 取得索引进行完整枚举。涉及向量的测试使用确定性的本地 embedding 夹具，证明数据库和通道合同，不代表真实 embedding 提供方性能。测试定义见 [覆盖用例](../../../tests/e2e/test_atomic_memory_fts_coverage.py) 和 [长查询用例](../../../tests/e2e/test_atomic_memory_long_fts_query.py)。

4.3.5.6 记录中的两次 native SQL 是未通过的能力探测，不能算成原生检索成功；真实数据路径观察到 16 次 legacy SQL。新版集成日志的 native 计数也包含能力探测，不将其等同于搜索请求数。证据为 [旧版兼容](evidence/20261105/legacy-final-v4356.json)、[4.6 覆盖](evidence/20261105/coverage-v2-v4601.json)、[4.6 长查询](evidence/20261105/runtime-final-v4601.json) 和 [5.0 集成](evidence/20261105/runtime-final-v501.json)，对应日志保存 pytest 分母。

## 中止、恢复与证据适用范围

`full` 在完成 4.6.0.1 的全部普通问题规模后，以实验级 `OperationalError 4012` 中止。中止前 232 次方法调用全部成功，其中 112 次小夹具、24 次性能预热、96 次正式搜索；错误发生在切换数据集的非计时阶段。该版没有保存具体维护 SQL，不能进一步声称已定位到某张表或某个算子。该次 5.0.1.0 尚未创建数据库，后续未生成槽位不计为 skipped。原始 [outcome.json](evidence/20261105/full/outcome.json) 和 [errors.jsonl](evidence/20261105/full/errors.jsonl) 均保留。

后续维护操作使用独立的 180 秒限制并恢复、核验查询的 10 秒限制；只补跑缺少的数据集，没有重复采样已成功的 4.6 普通问题。受控 fixture 在执行数据库测试前，还通过生产 `prepare()` 发现并修正了超过 8,192 字节的重复正文：最长正文重复两次，其他短正文仍可重复四次。该无效 fixture 没有作为有效测量数据。

controlled 的三次方法错误均为基线预热 `4012`；每次重连后恢复 10 秒限制，并连续两次确认本实验没有活跃或 `SESSION_KILLED` 会话后继续。日志未观察到等待超出 180 秒的情况。它以退出码 4 标明“完成但有性能失败”，不能将其写成整个测试零错误。证据见 [recovery.jsonl](evidence/20261105/controlled/recovery.jsonl)、[drain.jsonl](evidence/20261105/controlled/drain.jsonl) 和 [outcome.json](evidence/20261105/controlled/outcome.json)。

本轮没有 HTTP 上的 OceanBase 负载测试、真实 embedding 服务性能、并发、重启恢复或长期稳定性测量，也没有 p95/p99 估计。小样本中位数与固定合成数据只能支持这里列出的规模、版本及场景；无法验证用户原始 728 次 HTTP 500 的逐条原因，或外推任意数据分布下的 SLA。

三个性能运行实际创建的四个专库均已删除并读回确认不存在。所有测试进程结束后又进行了独立只读清理核验，覆盖开发探针、实机用例及性能运行的 **29 个实验库**：全部不存在，已记录的实验会话也没有残留。该核验只针对本轮记录的资源，不代表检查了实例上的全部业务会话。见 [cleanup-verification.json](evidence/20261105/cleanup-verification.json)。

## 复现与证据

runner 为 [benchmark_oceanbase_fts_native.py](../../../scripts/benchmark_oceanbase_fts_native.py)，协议与参数见 [README.md](README.md)。基线模块 SHA-256 为 `658ed88bd83ab3318ab300e7c5042061dd4f8821332485df5cb5cf01006cc438`；语料 SHA-256 为 `8f78f104eaa749c931df04e9b752a477d3a8b471f5e3432374d54af08e20cf52`。被测 native 生产模块的指纹为 `1f47186fba15ef7f6b7048bf1fd115960d40c785323a680ea845679019e3b8be`。两实现共享当前类型、schema 和 Analyzer，基线不是完整旧包的独立安装。每次运行的 `experiment.json` 保存各源码的完整指纹。

三个运行的 runner 指纹不同：`full` 为 `1596f857…`，`controlled` 为 `1a51f4c1…`，`corpus-v501` 为 `d72e2884…`。变化限于维护超时/记录、受控夹具长度、补充恢复探针及分支变量初始化；普通问题的语料和生产查询方法未变。新生产模块用上述源码指纹标识，`head_commit` 记录的是基础 checkout 提交。

本轮工作目录为 `/data/codex-tmp/ob-fts-native-20261105/worktree`，解释器为 `/data/codex-tmp/ob-fts-native-20261105/venv/bin/python`，`TMPDIR=/data/codex-tmp/ob-fts-native-20261105/tmp`。命令中的私密连接文件仅在本机脚本内读取，不纳入证据。

```bash
export TMPDIR=/data/codex-tmp/ob-fts-native-20261105/tmp
PY=/data/codex-tmp/ob-fts-native-20261105/venv/bin/python
CONNECTIONS=/data/codex-tmp/ob-fts-versions-20261104/connection-private.json

"$PY" scripts/benchmark_oceanbase_fts_native.py \
  --connection-file "$CONNECTIONS" --output /data/codex-tmp/ob-fts-native-20261105/full

"$PY" scripts/benchmark_oceanbase_fts_native.py \
  --connection-file "$CONNECTIONS" --datasets controlled \
  --output /data/codex-tmp/ob-fts-native-20261105/controlled

"$PY" scripts/benchmark_oceanbase_fts_native.py \
  --connection-file "$CONNECTIONS" --versions v501 --datasets corpus \
  --output /data/codex-tmp/ob-fts-native-20261105/corpus-v501
```

默认 `--sizes 100,1000,10000 --cycles 2`，基线为上述冻结提交，corpus 为本目录文件。运行必须使用新的输出目录与专用实例；runner 创建随机专库，不能指定业务库。连接配置不随报告分发，复现需自行提供。原始运行目录为 [/data/codex-tmp/ob-fts-native-20261105](/data/codex-tmp/ob-fts-native-20261105)。
