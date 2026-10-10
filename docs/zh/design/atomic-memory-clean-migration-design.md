# Memory 到 Atomic Memory 的停服迁移详细设计

本设计对应[总体方案](atomic-memory-clean-migration-overview.md)，规定 entry 历史、证据、持久化引用、权限、任务和检索数据的转换方式。目标是将旧 Memory 从公共制品空间移到离线归档后，新版服务只通过标准 Atomic 接口完成读取和生成。旧专用表暂时保留，归档和旧表由后续版本清理。

代码依据为 [PR 1857](https://github.com/oceanbase/powercontext/pull/1857)。迁移由 `powercontext server atomic-memory-migrate` 执行，备份、停服和数据库锁沿用 [RFC 1771](https://github.com/oceanbase/powercontext/pull/1771) 的维护约定。旧内容保留在归档表和旧 entry 表中，不物理清理旧历史；后续版本按保留条件单独清理。

## 1. 数据与兼容边界

---

### 1.1 需要保留的数据

- 每条旧 entry 的全部正文版本，包括 inactive 和已 compact 条目。
- 每个版本原有的直接 Source、精确 Artifact 依据，以及能明确归属的手工编辑 Source；整集合关系按第 5.2 节归档。
- 当前生命周期状态、正式 Owner、entry 标签和共享授权。
- Source ID、journal position、已有事件时间、消费 cursor 和已接受任务的处理进度。
- 其他制品和结构化 Source 中能转换为新精确引用的证据关系；纯历史信息迁入显式无类型字段。



### 1.2 不再提供的数据模型

- 旧 Memory 容器、集合 head、全量 manifest 和集合版本查询。
- 旧 `entry_version_id` 的在线解析、永久版本别名和身份映射表。
- 集合级 CAS、changes、capacity、compact 以及集合状态变化区间查询。
- 依赖旧 Memory 数据才能成立的运行时证据桥接。
- 公共 `MemoryCitation` 类型、公共表的 `memory_citations` 列，以及业务运行时的旧引用解码路径。

精确 entry 引用转换后仍指向同一历史内容。真正的整集合引用归档后从在线依据中移除，不展开成员或 Source。删除后会违反业务证据约束的记录按第 5.2 节预检阻断，不承诺所有合法旧数据都能直接升级。迁移只处理已知的结构化字段，不对自由文本执行 ID 字符串替换。

## 2. 存储清单



### 2.1 Atomic 功能已有的目标存储

本节列出数据迁入的位置。`pc_atomic_memory_states`、`pc_atomic_memory_current` 属于 Atomic Memory 功能本身，迁移向这些表导入数据并重建投影。无鉴权部署的 Owner 约束按第 7.1 节调整，不新增第二套状态或投影。


| 表                                                                    | 迁移职责                                                  |
| -------------------------------------------------------------------- | ----------------------------------------------------- |
| `pc_artifacts`                                                       | 写入 `atomic-memory` 的全部正文 revision；转换其他 Family 的结构化旧引用 |
| `pc_artifact_heads`                                                  | 写入每条 Atomic 当前 head 及治理摘要                             |
| `pc_artifact_lineage_sources`                                        | 保存 entry 的直接 Source 依据                                |
| `pc_artifact_lineage_artifacts`                                      | 保存转换后的普通 Artifact 依据                                  |
| `pc_atomic_memory_states`                                            | 保存当前四态、状态版本和合并去向                                      |
| `pc_atomic_memory_current`                                           | 保存在役记忆的当前检索正文、标签和适用的向量                                |
| `pc_access_owners`、`pc_access_relationships` | 迁移已有 Owner 和全部合法 entry binding；保留关系身份和权限 |
| `pc_artifact_tags`                                                   | 将 entry 标签转换为 Atomic Artifact 标签                      |




### 2.2 需要转换引用的数据

这些都是原有功能。迁移转换旧引用及旧类型字段，不重新生成制品。既没有旧字段、也不受依赖摘要变化影响的数据保持原值。

| 载体 | 本次处理范围 |
| --- | --- |
| 普通 Artifact 历史 | 公共 lineage 中的旧 Memory 引用及 `memory_citations`；真正的整集合引用按第 5.2 节处理 |
| Handoff 全部历史 | 转换 `state[].citations`、`next_action.citations`、`omissions[].citation` 中的旧引用；已发布的 Handoff 同样处理，正文变化时同步已有发布摘要 |
| `pc_sources` 中的结构化 Work 内容 | 转换 Task Outcome、WorkContract、CurrentWorkHandoff 等字段里的精确旧引用；整集合和纯历史引用移出有效证据字段 |
| `pc_recurrence_match`、`pc_recurrence_observation` | 仅同步已关联上述改写条目、且依赖其摘要的记录；不新建业务事件 |
| Candidate | 转换 `artifact_refs` 和 `memory_citations` 中的实际旧引用；Dream 产物通过运行记录定点定位。保留 proposal 和 target/result；删除集合引用后无法满足证据约束的记录在预检时阻断 |
| 尚未完成的 Dream | 检查是否依赖旧 Memory；受影响任务在迁移前由旧 Worker 处理完，不默认转换整套执行快照 |
| 终态 Dream、历史回执和操作记录 | 迁移时将旧请求、快照等历史内容移入显式无类型字段，再由新的历史记录格式读取 |

所有仍由业务模型解释的引用都使用新格式。历史内容只有迁入第 5.6 节的显式无类型字段后才能保留原值；业务代码不再保留旧引用类型或解码器。

使用各已知载体的明确转换规则，不递归替换任意 JSON。对于内置 Artifact 正文，当前需要专门处理的类型是 Handoff；不预设其他 Family 都有待改写的正文引用字段。

### 2.3 本次隔离与后续保留


| 数据                                                 | 本次迁移的处理                                      |
| -------------------------------------------------- | -------------------------------------------- |
| `pc_artifacts` 中 `family=memory` 的全部历史行            | 原值复制到 `pc_memory_artifact_archive`，核验后从公共表移出 |
| 旧 Memory 的公共 head、lineage                          | 保存原始快照到归档元数据，再从公共表移出                         |
| 旧集合/entry 的标签、Owner，以及会被改写的授权和幂等记录                 | 先保留原值快照；entry 数据迁到 Atomic，剩余旧目标元数据从公共表移出     |
| `pc_memory_entry_versions`、`pc_memory_entry_heads` | 暂时保留为离线旧数据；解除指向公共制品表的两个集合版本外键，不新建归档外键 |
| `pc_artifacts`、`pc_artifact_candidate_versions` 的 `memory_citations` 列 | 完成引用转换和核验后删除；普通依据统一使用 ArtifactRef |
| SQLite 旧 Memory FTS/vector 专用表与索引                  | 保留期内不使用、不重建，随旧专用数据在后续版本清理                    |
| OceanBase/seekDB 旧 entry 全文索引和向量表                  | 同上                                           |
| Source、Source journal、cursor、supervisor 数据         | 保留并继续承担原职责                                   |
| `pc_memory_source_windows`                         | 仍用于消费窗口时保留；它不是旧 entry 存储                     |


正常 Runtime 不注册旧 Memory 持久化类型，不读取、创建或重建旧 entry 索引。统一 schema 检查把归档和旧专用表识别为登记过的保留对象，不误报成未完成迁移。

### 2.4 唯一新增的归档表

`pc_memory_artifact_archive` 是本次迁移新增的业务归档结构，不是迁移控制表或在线映射表。


| 字段                                        | 内容                                                   |
| ----------------------------------------- | ---------------------------------------------------- |
| `scope_id, family, artifact_id, revision` | 原始四元主键，`family` 固定为 `memory`                         |
| `content`                                 | 原集合正文的原始字节，包含 manifest 和 changes                     |
| `memory_citations`                        | 原列值，保留 NULL 和原始编码                                    |
| `metadata`                                | 带格式版本的归档元数据，保留该 revision 的有序 Source/Artifact lineage 和被移除的入向集合引用 |


只有原 head 所在的归档行额外保存旧 head、该集合和 entry 的标签、Owner、授权及相关幂等记录原值，避免在每个 revision 重复保存当前元数据。原值指本次维护开始时的数据；对已经做过旧导入的库，不承诺还原第一次导入前已被改写的记录。先完整生成并核对这些快照，再执行会修改授权或删除公共元数据的步骤。

元数据采用冻结的格式，保留原列值、NULL、顺序和二进制值的可逆编码。公共外键不指向 metadata；正常 ArtifactRepository 也不解码这张归档表。

被删除的整集合关系记录在目标集合 revision 的 `metadata.incoming_references` 中，保存原表名、引用方完整身份、原字段路径或 ordinal、原目标和原始引用值。先核对这些记录，再删除在线关系；重跑按原位置去重，不另建溯源表。

旧专用表停止写入后，解除它们指向公共制品表的外键：


| 关系                                                                             | 迁移后                |
| ------------------------------------------------------------------------------ | ------------------ |
| entry versions 的 `(scope_id, family, memory_artifact_id, created_in_revision)` | 删除外键，保留原字段供离线对账 |
| entry heads 的 `(scope_id, family, memory_artifact_id, head_revision)`          | 删除外键，保留原字段供离线对账 |
| entry heads 到 entry versions                                                   | 保持原外键              |


SQLite 通过受控表重建解除约束，OceanBase/seekDB 通过显式结构迁移删除约束。迁移前后核对数据，不为只读旧数据重建指向归档表的外键。

公共 heads、lineage、publication 和 Candidate 的外键仍只引用新业务数据。公共 lineage 指向旧集合的边先归档后删除，不留下悬空外键，也不改指归档表。归档和旧专用表仅供离线核验，不提供在线读取。

## 3. 身份和版本转换



### 3.1 稳定 ID

沿用已经使用的确定性算法：

```python
identity = json.dumps(
    (scope_id, old_memory_id, entry_id),
    ensure_ascii=False,
    separators=(",", ":"),
)
atomic_id = "mem-" + uuid5(LEGACY_ENTRY_NAMESPACE, identity).hex
```

固定 namespace 和序列化规则，避免重新运行得到不同 ID。不同集合中同名 entry 不会被当成同一逻辑条目。

### 3.2 版本与正文

`entry.version` 已经是从 1 递增的整数，`entry_version_id` 才是随机字符串。`Atomic.revision = entry.version`，无需把随机串转换成数字或另行编号。保留旧 `kind` 和 `text`，不调用模型，也不使用新版正文规范化来改写旧内容。离线迁移解码和摘要核验使用冻结的旧格式；新写入继续执行当前内容校验。

`created_in_revision` 用于迁移时定位产生本版本的旧集合操作；它不是 Atomic revision。随机 `entry_version_id` 只参与离线迁移映射，完成后不持久化为 Atomic 的新别名；原值随保留的旧版本表存档。

在旧 Memory 移出公共表前完成以下对账：

1. 同一 entry 的版本从 1 连续增长，前驱关系和 entry 身份一致。
2. manifest 中的指针能对应到正确的 entry version，摘要一致。
3. 当前或 compact 前最后一次出现的指针能解释最终状态，不能简单取最大 version 掩盖旧数据错误。
4. 每个旧内容版本在新模型中恰好有一个对应 revision，正文和依据符合转换规则。

这些核验使用已经批量读取的版本和 manifest 数据。不会对每个引用调用一次正常业务读取接口。

例如，旧引用中的 `entry_version_id=mem_ver_xxx` 在版本表中对应 E 的 `version=3`，迁移后就引用对应 Atomic 的 `revision=3`。前后都是精确点查同一份历史内容。查询最新版本时不指定 revision，按当前 head 读取，与这项历史引用转换分开。

### 3.3 生命周期


| 旧条目最终状态                | 新状态       | 恢复能力   |
| ---------------------- | --------- | ------ |
| active                 | active    | 正常使用   |
| inactive，仍在 manifest 中 | forgotten | 可恢复    |
| 已 compact              | retired   | 不可直接恢复 |


旧 Memory 没有记录合并目标；其去重操作只会停用条目。迁移按原记录保留停用状态，不根据正文相似度或停用顺序推断合并关系。已导入 Atomic 后发生的合并按第 9.6 节保留。

状态只依据旧 manifest 和操作记录确定。无法核实条目为何不再出现在 manifest 中时，报告历史缺失，不直接推断为已 compact。

首次导入沿用当前迁移算法：state version 从 0 开始，`deactivate`、`reactivate`、`compact` 每次加 1，`add` 和正文 `revise` 不增加它；公共 head 的治理 generation 初始化为相同值。它不等于 entry version 或集合 revision。已经导入并继续演进的对象保留当前版本。旧集合的纯状态变化时间线不另外建立存储。

### 3.4 迁移期间的版本对应关系

旧 `pc_memory_entry_versions` 已同时保存 `entry_id`、`entry_version_id` 和整数 `version`。迁移按需要批量读取，不另写一张映射表：

```sql
SELECT entry_id, entry_version_id, version
FROM pc_memory_entry_versions
WHERE scope_id = :scope
  AND memory_artifact_id = :memory;
```

对每行计算确定性 Atomic ID，即得到：

```text
(scope, old_memory_id, entry_id, entry_version_id)
    → (atomic_id, revision=version)
```

同一批引用可以在内存中复用这份对应关系。旧引用中的集合成员关系由归档 manifest 核验；失败重跑时从归档集合和旧版本表重新读取即可。不能假设随机版本 ID 跨所有 Scope、集合全局唯一。

归档和旧版本表即使仍存在，正常 API 也不利用它们解析旧精确版本。历史证据使用转换后的 ArtifactRef；旧逻辑 ID 查询最新版本仍按第 10 节在 API 层适配。

## 4. Source 和 Artifact 依据



### 4.1 直接 Source 依据

每个 entry version 的 `source_refs` 写入该 Atomic revision 的公共 Source lineage。Source 的原始身份、业务内容和 journal position 不变。

不再添加“产生本条 entry 的旧集合 revision”作为运行时锚点。也不为兼容额外增加前一 Atomic revision 引用；版本号已经能表达本条记忆的内容演进。原始依据里本来存在的 Artifact 引用继续保留并转换。

### 4.2 手工编辑 Source

旧 Generic Artifact create/replace 入口会生成 `lineage_only` ContentSource，并将它挂在集合 revision 上；entry version 不一定带上这条 Source。`MemoryService.remember(entries=...)` 本身不会自动生成这种操作 Source。对于已有操作 Source 的版本，迁移补齐方法为：

1. 根据 `created_in_revision` 找到创建该 entry version 的集合操作。
2. 从该集合 revision 的 Source lineage 中选择人工写入 Source，核对其 `internal.operation` 和精确 `internal.target`。
3. 只补给该操作实际创建或修订的 entry 版本；同一操作修改多条 entry 时，可以共同引用同一条 Source。
4. 不把集合里其他 entry 的抽取材料一并复制过来。

Source 的旧 `internal.target` 描述当时编辑的集合，迁入第 5.6 节的无类型历史字段，在线读取不再按 ArtifactRef 解释该 target。正常新写入仍按当前规则校验 Source 资格，历史导入由冻结迁移资源处理。

没有操作 Source 时，保留 entry 自己已有的 Source 和 Artifact 引用。如果 entry 的两类引用都为空，也没有能归属到该操作的 Source，新 Atomic revision 的依据就保持为空。这是合法历史，不补造 Source、时间或旧集合锚点。

已有引用却找不到目标时，报告具体缺失对象，不能把它当作原本没有依据。已有操作 Source 中记录的时间按原值保留；缺失的编辑时间保持未知，不能用迁移时刻填充。

### 4.3 原有 Artifact 依据

非 Memory 引用保留精确 Family、ID、revision。旧 Memory 引用按第 5 节转换。

迁移后，普通 EvidenceResolver 读取新 Atomic 的公共 lineage 即可。删除 `read_imported_memory_evidence` 及识别旧集合锚点的特殊分支。

## 5. 持久化引用转换



### 5.1 精确 entry 引用

```text
输入：(scope, memory_ref, entry_id, entry_version_id)
查明：该集合历史版本中的精确 entry，以及 entry.version
输出：ArtifactRef(atomic-memory, deterministic_id, entry.version)
```

输出永远对应原来那一版，不跟随新 head。即使该 Atomic 当前已遗忘、被合并或退役，历史引用也保持原 revision；读取是否允许继续遵循现有权限和证据规则。

### 5.2 整个集合的引用

真正的整集合 ArtifactRef 没有单条记忆的对应目标，按以下规则处理：

1. 把原 lineage 行的引用方、目标集合 revision、ordinal 及原始值写入集合归档的 `metadata.incoming_references`。
2. 核验归档记录后，删除这条在线 lineage 行。Candidate 的 `artifact_refs` 等结构化字段中若有同类关系，也先归档原位置和原值，再移除在线关系。
3. 不展开集合成员，不改指其中任意 Atomic，也不把集合的 Source 复制给引用方。

例如 Experience X@2 原来只通过 M@8 追溯来源，迁移后这条关系可离线从归档查证，在线 X@2 不再沿 M@8 取得依据。X@2 自己原有的直接 Source 和其他有效依据保持不变。迁移前先检查删边后的记录是否满足对应业务模型的证据约束。

若某条 Handoff statement、已验证的 Work claim/check 或 Candidate 只以整集合为依据，移除后可能违反非空证据约束。这类记录可以由合法接口写入，不属于损坏数据。预检覆盖全部相关历史版本，在改写业务数据前统计数量，并列出记录身份、版本、字段位置和原引用。

Handoff statement 和 Work claim/check 的 citation 是带 `kind` 的精确引用，`kind=memory` 总是指向单条 entry 版本，转换后仍是精确依据，不会因删除集合引用而变空。Task Outcome 的 `produced_artifacts` 没有非空约束。因此实际会被阻断的只有 Candidate：某个版本的 `artifact_refs` 只含整集合引用，且没有 Source 或可转换的精确 citation。

数量为零时继续迁移。存在此类记录时，迁移命令通过 `--decisions` 接受维护者提供的决策文件，每一项以 `(scope_id, candidate_id, version)` 和字段 `artifact_refs` 定位一条阻断项，动作只能是 `replace`：用指定的精确 ArtifactRef 列表作为该版本的依据，目标必须在同一 Scope 中存在且不是旧 Memory 集合。`declare` 或 `reject` 都无法让历史版本重新满足 Candidate 的证据约束，因此不提供。

每一项处理前，原字段值、原位置和所用决策一并写入集合归档的 `metadata.incoming_references`。`--action plan` 只读执行同一预检，列出全部阻断项及决策文件覆盖情况；决策文件未覆盖全部阻断项、引用了不存在的阻断项、重复，或动作不是 `replace` 时，迁移在改写业务数据前停止。

本次不为 Handoff、Work 或 Candidate 增加历史条目变体。仅修改当前 head 或新增记录不能解决旧版本中的阻断项；决策必须覆盖仍需迁移的历史版本。迁移不会在没有决策时删除原内容、清空必需证据或伪造 Source，也不会改变 verified 或审核状态。

当前 Atomic 分支存在旧 Memory 特例，会沿集合 lineage 追溯 Source。本次删除该特例，明确停止提供整集合的在线溯源，不把删边描述成原行为完全不变。公共 lineage 的外键也要求在移出集合前完成关系清理。

第 4.1、9.6 节可识别的旧 Atomic 导入锚点，以及第 5.4 节可从正文精确 citation 还原的 Handoff 集合锚点，先还原为实际精确依据。它们不作为无法定位 entry 的整集合关系直接丢弃。

当前合法 Candidate target/result 指向候选自身的 Family，通用发布也不支持 Memory 集合，不为这两类对象设计集合转单条的正常迁移路径。若预检发现异常记录，按不支持的数据形态报告。

### 5.3 Artifact lineage 和 Experience

把 `memory_citations` 中的精确 entry 引用转换为普通 Artifact refs，按稳定顺序去重并重排 ordinal，保留其他 Source、Prompt 和 Artifact 依据。原有 `lineage.artifacts` 中真正的整集合引用按第 5.2 节单独列出；不能直接展开为成员。

转换并核验后，删除 `pc_artifacts` 和 `pc_artifact_candidate_versions` 的 `memory_citations` 列，业务读写只使用 SourceRef 和 ArtifactRef。归档中的原值由离线迁移资源管理。迁移不调用正常的“修订制品”操作，因此不会额外生成一个业务 revision，也不会创建迁移 Source。

例如，Experience X 引用旧 entry E，Skill K 引用 X。转换后 X 改为引用 Atomic A，X 的编号、revision 和正文都不变，K 继续引用 X，无需沿下游逐个修改。仅 lineage 改变时，已有发布副本的正文和摘要也不变。

全库已知引用的检查与 Dream 产物定位分开：

- 普通 Artifact 检查 `memory_citations`，公共 lineage 检查 `upstream_family=memory` 的记录。
- Candidate 检查 `artifact_refs` 和 `memory_citations`，不读取无关的 proposal。
- Handoff 的引用嵌在正文中，按第 5.4 节处理。

前两项只读取身份和引用字段，按主键分页；发现实际旧引用后才转换。当前没有完整的引用反向索引，这些检查仍可能遍历全部相关行。空 `memory_citations` 也可能保存为编码后的空列表，不能用 `IS NOT NULL` 判断是否含旧引用。

这一步覆盖通过手工提议或复制内容写入的旧证据。例如，用户把 E 的 citation 复制进另一个 Experience，却没有保留指向 X 的关系，这条记录就不能通过 Dream 找到。只处理 Dream 结果不能代替全库已知引用字段的核验。

### 5.4 Handoff

处理全部历史 revision，不能只处理 head。需要转换：

- `state[].citations`。
- `next_action.citations`。
- `omissions[].citation` 中的精确 entry 引用，保留原有原因。hint 会再次验证这些引用，不能把它们都当成只供展示的历史文字。

精确 entry citation 从 `kind=memory` 改为 `kind=artifact`。Handoff 的旧 lineage 可能只记录 citation 所属的集合，正文才保存具体 entry，因此要根据正文还原精确依据。例如正文只引用集合里的 A，迁移不能把同集合的 B 也加进依据。

本地 Handoff 根据转换后的 `state/next_action` citation 重建对应的 Memory 依据，保留原有 Prompt、Source 和其他 Artifact 依据。正文若真正引用整个集合，按第 5.2 节单独处理。omissions 原来不属于直接依据的，不因迁移而新增为生成依据；发布副本不额外生成目标 Scope 下的本地 lineage。

当前正文没有完整的引用索引，omissions 也不能通过 lineage 反查，所以按 `(scope_id, family, artifact_id, revision)` 分页扫描 Handoff 历史，读取后只更新需要转换的行。

```sql
SELECT artifact_id, revision, content, memory_citations
FROM pc_artifacts
WHERE scope_id = :scope
  AND family = 'handoff'
  AND (artifact_id > :last_id
       OR (artifact_id = :last_id AND revision > :last_revision))
ORDER BY artifact_id, revision
LIMIT :batch_size;
```

第一页不带游标条件。批次中涉及的旧 entry 版本一起查询或从已加载的迁移映射中取出，避免每条 citation 单独查库。

已发布的 Handoff 使用同一套转换规则，按已有 publication 来源确定引用所属的 Scope。正文中的 Memory 引用转成 Atomic 引用后，按发布模块原有的 `sha256(dump_model(content))` 算法同步相关 `pc_artifact_publications.content_digest`。来源地址、目标地址、发布幂等键和 Handoff 身份保持不变。迁移完成时核对来源正文、各副本正文和相关发布摘要一致，不另设一套发布迁移流程。

原始生成草稿摘要和 Prompt 摘要保留，它们记录当时的生成过程。数据库外保存的旧正文摘要不保证继续有效。转换结果须能按使用新引用的 Handoff 业务模型读取，不增加历史 statement 变体。

### 5.5 Candidate 和 Dream 产物

Dream 生成 Experience 的记录可通过现有字段定位：

```text
pc_dream_runs.payload.run.candidate
    → Candidate 当前 head
    → head.result_family / result_artifact_id / result_revision
    → 对应 Artifact 及同一 ID 的各个 revision
```

处理方式：

1. 分页读取 Dream 运行记录。旧格式终态运行记录全部按第 5.6 节改成显式历史格式，包括旧引用字段为空的记录；未结束且带旧引用的运行阻断迁移。
2. Dream 产生的 Candidate 和批准结果，与手工提议的记录一样，由第 5.3 节的通用引用检查覆盖：Candidate 版本按 `memory_citations` 非空或 `artifact_refs` 含 `"memory"` 筛选，Artifact 按 `memory_citations` 非空筛选，只读取身份与引用字段。
3. 转换候选证据中实际存在的旧引用，保留 Candidate ID、版本、审核状态、proposal 和 target/result。删除集合引用后无法满足证据约束的记录，必须已在第 5.2 节的预检中发现并阻断。
4. 相关 Artifact 的编号、revision 和正文不变；引用它的 Skill 或其他 Artifact 继续使用原地址，无需沿下游修改。

例如，Dream 从 E 提炼出 Experience X，之后又从 X 生成 Skill K。需要转换的是候选和 X 中指向 E 的证据，K 指向 X 的引用保持不变。单纯从 Source 生成、且没有旧 Memory 引用的候选和制品不改写。

通用检查不读取 Candidate proposal 或 Experience、Skill 正文，也不计算覆盖所有候选整行内容的处理状态摘要；筛选条件仍可能让数据库遍历全部候选版本和 Artifact 行，成本按第 11.3 节单独记录。

转换后，候选按对应入口现有的 ArtifactRef 规则校验证据和审批；新 Dream 可以通过这些 Experience 继续展开剩余的依据。迁移不为 Atomic 引用增加单独的生命周期或权限校验，公共入口已有的鉴权保持不变。

### 5.6 显式无类型历史字段

纯历史内容在停服迁移时写入 `historical_data`，类型为 JSON 值，可以保存对象、数组和标量。该字段只供展示和离线核对，不注册引用解析器，不生成 ArtifactRef，也不能成为生成、审批或任务重放的输入。

迁移用冻结的旧格式解码器读出原记录，把需要保留的原字段值移入 `historical_data`，再移除原来的强类型字段。新格式带明确的历史记录标识，运行时只读取普通元数据和无类型 JSON；禁止根据字段名或 JSON 形状猜测旧引用类型。不能把整段旧 payload 原封不动留在旧字段中，再让运行时尝试新旧两套模型。

| 历史载体 | 迁移后的表示 |
| --- | --- |
| 全部旧格式终态 Dream | 保留 run ID、状态、时间、结果等普通元数据；原 request、input manifest 及其原摘要移入 `historical_data`。旧字段即使为空也完成格式转换，不要求旧 request 通过新的请求模型校验 |
| 历史 HandoffReceipt | `unavailable_evidence` 中的旧 citation 是精确 entry 引用，按第 6 节与其他 Work Source 一样转换为 Atomic 引用；回执状态和证据列表保持有效，不需要历史格式。以 `kind: artifact` 引用整个集合的条目没有 Atomic 对应，按原值移入回执的 `historical_data`（格式 `powercontext.handoff-receipt-history.v1`），不留在强类型字段中；回执只因这些条目而不可用时，证据状态保持 `unavailable` |
| ContentSource 的旧 `internal.target` | 只标记 lineage_only 写入回执的目标，运行时不据此读取旧 Memory，按原值保留 |

上述历史数据写入各载体现有的 JSON 载荷，不新增历史表，也不为 Handoff statement、Work claim/check 或 Candidate 增加历史条目变体。失去必需证据的业务条目按第 5.2 节阻断升级。当前业务输入拒绝把这些无类型历史数据当作有效证据。

原 `prepared_digest`、模型输入快照摘要、Prompt 和草稿摘要描述当时的内容，随历史信息保留，不按今天的引用重新计算。历史查询返回显式历史字段；调用方若提交它来恢复执行，明确拒绝。

原有任务 ID 和幂等键关联保留。已完成请求的查询或幂等结果读取直接使用新历史记录及已保存的请求摘要，运行时不解码旧 request 或重建输入。旧请求模型里始终为空的 entry 引用字段会改变请求摘要，因此迁移在离线阶段删除未结束运行请求和输入清单中的空字段，并对未引用旧 entry 的请求按当前请求格式重算已保存的摘要，使相同请求的重试仍能命中原运行；引用过旧 entry 的请求无法再提交，保留原摘要。

仍参与 Work 连续性、recurrence 或审批的结构化内容按第 6 节转换，不能整条塞进无类型字段后跳过业务处理。无类型字段只保存退出业务解释的历史部分。迁移完成后，业务目录中 `MemoryCitation` 和 `memory_citations` 零命中；旧解码代码只允许存在于登记的离线迁移资源中，运行时不得导入。

## 6. 结构化 Source 与依赖摘要



### 6.1 需要转换的字段

结构化 Work Source 的读取也必须退出旧引用模型。需要检查的字段包括 Task Outcome 的 `observations`、`checks`、`produced_artifacts`，`WorkContract.facts[].evidence`，以及 CurrentWorkHandoff 的 `state`、`next_action` evidence。

其中的精确 entry 引用全部转换为对应 Atomic ArtifactRef；真正的整集合关系按第 5.2 节归档并移出有效引用字段，原文字和位置保留。纯历史的旧 target 或不可用证据按第 5.6 节迁入无类型字段。正常读取直接使用新格式，不保留解析旧模型的分支，也不能将这些 Source 统一判为无效后跳过。

Source ID、journal position、业务描述和实际记录的时间保持不变。引用转换使用冻结的 Source 格式；新写入仍执行当前准入规则。

### 6.2 recurrence 同步处理

WorkClaim/TaskCheck 的条目摘要包含 evidence。只有条目引用确实改变、且已有账本关联这条记录时，才同步修改受影响字段。没有对应账本记录时不创建记录，其他 Source 和条目保持原值。

以一条 Task Outcome 及其受影响账本记录作为转换单元：

1. 生成新的结构化 evidence。
2. 按该版本的序列化规则重新计算受影响条目的 `item_digest`。
3. 更新引用这些条目的 locator、`failure_item_digest` 和 payload。
4. 重新计算直接依赖上述摘要的 `match_key`、`recurrence_match_digest` 和 `observation_id`。
5. 保留原匹配结果、业务判定和处理进度，不重新执行匹配，不新增一次观察。

当前 recurrence 的匹配是确定性规则，不是模型判断。`selection_key`、`verdict_key` 和不依赖改写条目的 selected 事件保持原值。更新后的唯一约束若发生冲突，需要判明是否为同一业务记录，不能覆盖另一条记录。

Source 与受影响账本在同一个事务中转换。失败则一起回滚，避免 Source 已变而账本仍引用旧摘要。

### 6.3 保持原文的历史信息

ContentSource 的旧 `internal.target` 保留原值，自由文本中的旧 ID 保留原文。运行时不通过这些内容读取旧 Memory，也不把它们恢复成旧对象。

不能把历史 receipt 的 `prepared_digest` 当作当前 Handoff 的摘要重新计算；它描述当时准备过的内容。若某条恢复任务必须重放旧的结构化请求，则按待处理任务规则处置。

外部客户端用同一 Source ID 重发带旧引用的原始 payload，可能不再满足新的写入契约。接口明确拒绝旧结构化 Memory 引用，不维护另一份原 payload 来兼容这种重试。

## 7. 权限、标签和幂等



### 7.1 Owner 和共享授权

Atomic 的 Owner 要求与其他制品对齐：

| 部署方式 | Owner 处理 |
| --- | --- |
| 未启用鉴权的 HTTP 服务或 SDK | 创建和修改不要求 Owner；无 Owner 的记忆照常进入检索投影和抽取候选，不补造 `local-runtime` Owner |
| 启用鉴权 | 继续使用现有授权服务及 Owner 规则，不因迁移放宽权限 |

按 `(scope, memory_id, entry_id)` 迁移已有 Owner，不从集合 Owner 推导，也不把无 Owner 的记忆分配给迁移执行人。合法的无鉴权旧库可以保持 Owner 为空；缺失了启用鉴权时必需的授权数据，则按既有规则报告。

实施时同步调整 Atomic 自身的创建、修改检查，current 的加载、写入和重建，以及抽取候选过滤。`pc_atomic_memory_current` 不再保存 Owner 列，抽取候选也不按调用者 Owner 预先过滤，逐条由公共授权判定能否读写；启用鉴权时 Owner 仍由公共授权服务建立和校验。不能只放宽导入程序，否则导入后的记忆仍可能在发布投影或抽取时被拒绝。

删除 `atomic_memory_security.py` 及其中的独立授权规则，使用其他制品已经采用的公共授权服务和事务回调。合并、恢复和后台抽取的实际读写目标接入公共检查；投影构建中的标签、Owner 数据读取归入对应持久化职责。无鉴权不额外比较调用者与 Owner，不保留 Atomic 专属的全局权限版本锁，也不修改 Casbin 或公共授权算法。

旧 Memory 的公共 access profile 要求 `memory_entry` selector；缺少 entry selector 的请求会被 `memory-entry-selector` 拒绝。允许授予的角色只有 `artifact.viewer`，Atomic 也支持这一角色。因此合法的旧共享 binding 都以 entry 为目标，全部自动迁移，不存在需要人工选择成员的合法集合级 binding。

共享关系保留 `binding_id`、主体、角色、有效期、撤销状态和授权来源，只转换资源身份及由它计算的键。例如，`bind_123` 原来授权读取旧 entry E，迁移后仍是 `bind_123`，授权目标改为对应 Atomic A。已过期或已撤销的 binding 仍是合法历史，按原状态迁移。

旧 Memory 授权 selector 请求在进入幂等重放前返回 unsupported，因此不再兼容旧 `binding.create` 请求重放。其 `payload_hash` 保留为原请求的历史摘要，不随目标资源改写，也不要求迁移 verify 用新资源重新算出同一摘要。新 Atomic 授权请求复用旧创建键时按不同请求返回冲突；确需新建授权时使用新键，已迁移的 binding 不需要重新创建。

revoke/replace 继续通过原 `binding_id` 和授权关系版本操作，其摘要不受资源转换影响。binding 的人工处理范围仅限违反原授权契约的损坏数据：目标 entry 在完整旧数据中不存在、selector 缺失或非法、角色不在原允许授予的列表中。预检列出具体记录并阻断升级，不把合法共享关系转为人工重建任务。底层直接写库可以绕过公共入口校验，因此仍需检查这几类损坏记录。

### 7.2 标签

entry 标签转换为 Atomic 的 Artifact 标签，并刷新当前投影中的标签字段。迁移后通过新 Atomic target 使用公共标签接口和新 ETag。

旧 entry 或集合标签入口、selector 和旧 ETag 包装停止支持，不在 server 中继续维护两种标签身份。旧请求即使携带原 ETag，也在写入前返回 unsupported；调用方改用 Atomic 身份重新读取标签后再修改。

旧标签规范与 SQL 都区分 `artifact` 和 `memory_entry`：集合标签只匹配集合，entry 的列表和搜索只匹配自身标签，没有集合到 entry 的继承。

例如 M 有标签 T，E 属于 M 但没有标签，旧 entry 搜索按 T 不会返回 E。因此仅把 E 自己的标签迁到对应 Atomic；集合标签保存在归档元数据，不丢弃，也不自动复制。若产品决定把集合标签下发给所有 Atomic，应作为明确的检索语义变化讨论，不能称为原有行为。

这与 Owner 是两件事：标签不承担授权，Owner 仍按每条 entry 的既有关系迁移。[RFC 1467](../rfcs/1467_artifact_tags.md) 对两类标签目标及不继承的边界已有规定。

### 7.3 其他重试记录

仍受支持且会重放的请求，其摘要与被转换内容一起处理。已经停止支持的旧请求不重放，摘要作为历史保留，不为它重建兼容输入。

无法跨版本继续执行的任务在迁移前处理完。终态记录含旧类型时，迁入第 5.6 节的显式历史格式，保留原摘要；不能通过保留旧运行时解码器来延续读取。

## 8. Source 进度和待处理任务

保留 cursor、generation、高水位、pending/flush 请求和已接受任务的进度。本次暂留 `memory` 调度 Family 和 `memory-source-window` binding，处理器生成 Atomic，不读取旧 Memory 容器。迁移不能清零 cursor 后重新消费全部 Source。旧调度名称的更名列入第 9.5 节的后续清理事项。

先停止提交新的受影响任务。若有依赖旧 Memory 的 queued/running Dream，在旧 schema 仍可用时由旧 Worker 处理至终态；之后再停止写入者、取得维护锁并按现有 fence 规则使旧 lease 失效。不能一边改 schema，一边让旧 Worker 排空任务。

| 对象 | 处理方式 |
| --- | --- |
| 仅保存 Scope、Source 范围和调用进度的生成任务 | 保留，交给已适配的处理器继续执行 |
| Candidate | 按第 5.5 节定点处理 Dream 产物；其余已知旧引用按第 5.3 节检查。保留候选 ID、版本、审核状态、proposal 和 target/result；失去必需证据的记录在预检时阻断 |
| 旧格式终态 Dream（成功或失败） | 全部改成显式历史格式，包括旧引用字段为空的记录；get/list 读取新历史格式，不调用旧解码器 |
| 依赖旧 Memory 的 queued/running Dream | 迁移前处理完；当前没有取消 API，不承诺通用取消或跨版本快照续跑 |
| 未识别、且仍会执行的旧任务格式 | 计划报告具体对象和阻塞原因，不能假报可兼容消费 |

Dream 的 `refine_experience` 和 Experience Candidate 使用 Atomic 的普通 ArtifactRef；`derive_skill` 的制品输入仍只接受 Experience。带旧引用的未完成 Dream 在维护前完成，迁移不改写其执行快照来续跑。终态历史通过新历史格式展示；候选和制品中继续使用的证据转换为新引用。

自定义旧 Memory 抽取 Prompt、CandidatePipeline 和集合 WriteGate 属于应用集成配置，发布说明提示调用方按新契约调整，不把它们假定为统一存放在业务表中的迁移对象。

## 9. 迁移执行与失败重试



### 9.1 公共迁移版本与数据任务

RFC 1771 的迁移执行器目前只管理四张 Artifact 表的结构版本，拒绝完整的服务端数据库，也没有数据任务执行器。因此归档、导入、引用转换和移除旧对象由 `atomic-memory-migrate` 自己执行，所需的结构变更（建立归档表、解除外键、删除旧引用列）也在同一命令中完成；它不另建私有进度表，完成状态由实际数据核验。Atomic 状态表和当前投影是功能前置条件。

`apply` 的执行顺序为：

1. 只读预检：旧格式、版本链、Owner、授权回执，以及第 5.2 节的阻断项和决策文件。有阻断项时不写任何数据。
2. 建立归档表，按集合归档并核验，然后完成 A 的导入。
3. 核对 Source 进度和待处理任务未变后，执行 B 的引用转换、集合关系归档删除和 Dream 历史格式改写。
4. 解除两个旧 entry 表指向 `pc_artifacts` 的外键，不新建归档外键。新建数据库的表定义也不再包含这两个外键。
5. 旧集合仍在公共表时完整验收导入历史、当前投影、授权转换和全部引用转换；未通过则不移出任何集合。
6. 执行 C 移出公共表中的旧对象，再做残留检查后放行。

旧集合移出以第 5 步验收通过为前提，所以普通启动只需检查公共表残留即可确认迁移完成，不重复全量验收。

两张公共表的 `memory_citations` 列在引用转换验收和公共表移出之后删除。删列是迁移的最后一步，启动门禁把该列存在视为迁移未完成；已移出全部集合但尚未删列的数据库重新执行迁移即可补完回执、Dream 和删列步骤。

大量复制、引用改写和 embedding 不放在长 DDL 事务中。数据任务分批提交，失败后根据实际数据核验和重跑，不增加私有进度表，也不借用调度专属 schema/receipts。

公共版本符合要求但公共表仍有旧 Memory 对象时，迁移仍未就绪。普通启动用必要的存在性检查拒绝这种中间状态，不只看版本号，也不扫描全部归档历史。

旧专用表和归档均登记为保留对象。后续删除这些对象再提交独立的结构迁移和清理计划，符合 RFC 1771 的延后清理要求。

RFC 1771 执行器以后若支持完整数据库和数据任务，可把这里的结构步骤登记为公共修订；数据任务仍以实际数据核验作为完成条件。

### 9.2 阶段 A：归档并导入

1. 预检旧格式、版本链、Owner、引用、待处理任务和目标 ID 冲突；检查第 5.2 节的证据约束及第 7.1 节的损坏 binding。发现阻断项后停止，不进入业务数据改写。
2. 将旧 Memory 的全部 revision、lineage、head 和会被变更的元数据原值写入归档，逐项核验。归档先于授权改写及公共元数据移除。
3. 离线旧集合读取统一使用归档，entry 版本读取使用保留的旧表。按 Scope、容器和批次建立临时对应关系。
4. 先写入全部 Atomic 历史正文，再建立涉及其他 Atomic 的引用，避免引用目标尚未导入。
5. 每条 entry 的 head、state、已有 Owner、标签及授权一起提交；旧创建授权回执保持原值。
6. 从实际新 head 和状态准备、发布 current，完成导入对账后进入阶段 B。

完整归档须覆盖旧集合的全部历史 revision、每版 lineage，以及本节规定的 head 和公共元数据；不能仅凭归档中已有 head 行判断完成。开始改写公共数据前，逐项核对原记录与归档。

归档重复写入时按原四元身份比较原始内容及元数据，相同则跳过，不同则报告冲突，不覆盖归档。已经完整归档的集合，在后续步骤失败后直接复用原快照，不能把部分迁移后的权限或标签重新当成原值存入归档。

正文批次和 lineage 提交可以分开，因为服务始终停服。重试逐一比较已存在的确定性 ID、正文和引用；只补齐已证明属于本次导入的缺项。相同 ID 下出现其他正文时报告冲突，不覆盖。

head 已存在不表示 current 已发布。元数据提交后退出，重跑仍须补齐投影；使用实际保留的新 head、state、已有 Owner 和标签构建，不使用旧 entry tail 回退新演进。无鉴权部署允许 Owner 为空，非 active 目标确保没有 current 行。

### 9.3 阶段 B：转换引用并对账

按第 5、6、8 节处理。Dream 产物按已有运行记录定位，普通 Artifact 和 Candidate 分页检查引用字段；Handoff、结构化 Work Source 读取需要转换的正文。终态历史在这一步改写为显式历史格式，真正的整集合关系先归档原位置再从在线依据移除。

每次事务处理普通历史记录及其直接依赖的摘要，或一条 Source 及其关联账本。已发布的 Handoff 使用同一转换规则，完成后统一核对源、副本和发布摘要。无需把整条发布链或所有下游制品放进一个事务。

写入前比较读取时的 payload 或相关摘要，发现维护期间仍有写入则停止。旧格式转新格式，已经是目标格式的记录直接核验，不再次转换。归档内保留原始引用，不改写其历史快照。

阶段末检查所有已登记的引用和历史载体：业务字段不再保留旧格式，纯历史原值只存在于无类型字段或离线归档。确认归档、Atomic 导入和转换都完成后，删除两个旧外键及公共旧引用列。若 DDL 中途失败，重试逐项核对结构；已删除的列不再读取，直接用新 lineage 和归档核验已完成结果，不假设 DDL 整体回滚。

### 9.4 阶段 C：移出公共表并放行

旧专用表到公共表的外键解除后，按顺序处理：

1. 再次确认其他 Family 的运行引用已经转换，归档内容和元数据完整。
2. 清除所有旧 Memory 自身拥有的公共 lineage，避免旧集合相互引用阻碍移出。
3. 按集合移除已归档的旧 tags、Owner 等公共元数据；迁到 Atomic 的授权保留并继续生效。
4. 移除旧 Memory 的公共 head，最后移除 `pc_artifacts.family=memory` 的全部 revision。
5. 核验公共表中无旧 Memory 对象、指向旧集合的 lineage 或旧外键。新权威、投影和引用转换已在移出前验收，这里不重复。

不删除归档、旧 entry versions/heads 或旧专用索引，不向它们继续双写。公共表移出按批提交，失败重跑时通过归档与旧版本表重建所需对应关系；公共对象不存在且归档完整是已移出的状态，不是丢失数据。

新版启动要求公共 schema 就绪且旧公共对象隔离完成。存在未完成批次时拒绝启动；启动检查不自动处理剩余批次。

### 9.5 后续清理归档

保留窗口满足、确认旧程序和恢复职责不再需要这些对象后，由后续版本移除旧专用索引、entry heads、entry versions 和归档表。旧 heads 到 versions 的内部外键按依赖顺序清理，归档没有在线外键依赖。不删除 Source、cursor 或仍在使用的消费窗口数据。

`memory` 调度 Family 和 `memory-source-window` binding 的更名单独安排，需连同 cursor、generation、pending/flush 请求及已接受任务的关联一起迁移，并核对消费进度不变。不能随旧 entry 表删除这些调度身份或清零进度。

### 9.6 已执行过当前导入方案的数据库

按已知旧导入形态识别并修正历史 lineage：

- 确定性 ID、每个已导入正文 revision 必须与原 entry 一致。
- 旧集合 anchor 和仅为导入兼容加入的 predecessor 引用替换为直接依据。
- 新旧目标 lineage 两种已知形态都允许幂等重试；其他形态明确报告。
- 已导入记忆后来产生的正文版本、合并关系、遗忘/恢复/退休状态、state version、公共 head 治理摘要和标签不回退。
- 核验并保留已有正式 Owner；无鉴权数据允许没有 Owner。出现不同 Owner 时按现有不可替换规则报告冲突，不引入新的 Owner 转移机制。
- 已迁移共享关系的后续撤销、替换和有效期变化保持原值，不能重放旧授权使其重新生效。
- 后续版本如果仍引用旧 Memory，也纳入阶段 B 的通用转换。

这是针对可识别旧导入格式的维护升级，不是遇到任何 Atomic 数据都用旧数据库覆盖。

### 9.7 回退

失败时服务保持停止，可继续同一迁移，或按 RFC 1771 恢复升级前备份。归档保留旧 Memory 域的数据，但其他制品、Source 和账本也可能已被转换。不能仅把归档集合搬回公共表或删除新 Atomic 行，就声称数据库已整体回退。

## 10. API 与运行时调整



### 10.1 server 兼容入口

旧 Memory 兼容遵循 [RFC 1809 的升级与兼容条款](../rfcs/1809-atomic-memory.md#升级与兼容)，只保留在 server 请求适配层。它把可转换的输入交给标准 Atomic 服务，不在 Runtime、Python SDK 或公共领域模型中保留旧 Memory 服务。新 Atomic 的标签、授权、版本和生命周期接口按各自契约使用。归档表仅用于离线保管原始数据，不承担接口兼容。

| server 入口 | 保留的请求和语义 |
| --- | --- |
| `memory.get` | 在请求 Scope 中，用旧集合 ID 和 entry ID 计算 Atomic ID，读取当前 head；旧随机版本 ID 的历史点查 unsupported |
| `memory.list` | 列出 Scope 下的 Atomic，按新契约过滤和分页，返回新身份 |
| `memory.search` | 调用 Atomic 搜索，返回 Atomic 引用 |
| `memory.remember` | 仅接受可直接转换的新增内容；带旧集合 CAS、指定旧 entry 修订或旧对象证据的请求 unsupported |
| `memory.flush` | 进入 Atomic Source 消费链，保留原消费进度 |

精确历史读取使用新的 Atomic 接口及 `ArtifactRef(atomic-memory, artifact_id, revision)`。例如指定 `revision=3` 就读取第 3 版，不能为了兼容旧随机版本 ID 而改读当前 head。server 适配不访问归档或旧 entry 表。

除上述五个入口的可映射请求外，以下旧行为均在执行前返回 unsupported：

- `memory.revise/retire/capacity/compact/changes/cursor` 等其他旧 Memory 操作。
- Generic Artifact 中任何显式 `family=memory` 请求，包括列表、创建、替换、latest、exact 和 revisions。
- 旧集合或 entry 的 tags、ETag 包装、access selector 和授权创建请求。
- 旧集合 CAS、旧集合成员快照，以及包含旧查询语义的分页游标。

默认跨制品查询排除旧 Memory；显式把它放进混合查询时拒绝请求，不静默忽略。标准授权关系已经迁移，仍可通过原 binding ID 使用公共 revoke/replace 接口。

发布说明列出响应、分页和 Scope 查询范围的变化，并提供新 Atomic 调用方式。客户端需要调整，不能把保留五个路由描述成完整旧契约兼容。

### 10.2 删除旧类型和解码路径

业务模型、SDK 导出、server、HTTP 生成模型、运行时、证据解析和公共持久化代码移除 `MemoryCitation`、`memory_citations` 字段以及 Memory citation union 分支。OpenAPI 同步删除旧公共 schema，生成代码由契约重新生成。server 的五个薄适配入口只转换身份和新增内容，不复用旧领域模型。

迁移时完成所有强类型引用转换和显式历史字段改写，再发布不含旧模型的新业务代码。终态 Dream、回执等历史查询只读取新的历史记录格式和 JSON 值；禁止旧模型 fallback、按形状识别旧引用、运行时调用冻结迁移解码器或访问归档恢复旧类型。

新请求在模型调用或持久化前拒绝旧引用和 `family=memory`。这覆盖 Atomic 写入和合并、候选提议/修订/审批、Dream、Handoff 各写入入口，以及结构化 Work Source。unsupported 请求在幂等或 no-op 分支前同样拒绝。原始历史 JSON 只能作为历史字段展示，不能绕过这些检查重新进入执行。

转换后的 Atomic 引用采用对应入口现有的普通 ArtifactRef 检查。Experience 的 Task Outcome failure 引用检查只确认指定对象存在，不延续旧 MemoryCitation 的专属证据校验，也不为 Atomic 增加状态或权限分支。公共入口已有的调用者鉴权保持不变；Handoff 与普通 Artifact 的精确历史读取继续遵循各自契约。是否为所有 ArtifactRef 增加生命周期检查属于公共规则变更，不在本次迁移中处理。

### 10.3 删除运行时旧依赖


| 代码边界                                                        | 调整                                                             |
| ----------------------------------------------------------- | -------------------------------------------------------------- |
| `atomic_memory_compatibility.py`                            | 移除 Runtime 中的旧 Memory 兼容，确定性身份映射只由 server 五个适配入口使用 |
| `atomic_memory_legacy_evidence.py`                          | 删除导入锚点解析模块                                                     |
| `builtin/evidence/resolver.py`                              | 移除旧 entry 读取和导入证据特殊分支                                          |
| `builtin/persistence/handoff.py`                            | 不再调用旧 `MemoryService.validate_citation`                        |
| `builtin/runtime/relational.py` 的旧 frozen Memory 服务         | 不再向公共 Runtime 暴露旧集合精确读取                                        |
| `builtin/runtime/relational.py` 的类型及 writer 注册              | 正常 Repository 不再注册旧 Memory 持久化类型，不再注册 `MemoryManagementWriter` |
| `builtin/persistence/family_management.py` 的 Handoff writer | 不再为 Generic Handoff 写入构造旧 Memory backend                       |
| `builtin/persistence/records.py`                            | 关闭旧集合 exact/revisions/current entry 等存储读取                      |
| `builtin/runtime/recall.py`                                 | token 统计和证据定位不再调用旧 citation resolver                           |
| `builtin/runtime/composition.py`                            | 不创建或重建旧实体表和检索索引；搜索能力从 Atomic 获取；校验公共 schema 及旧公共对象隔离是否完成       |
| `atomic_memory_rebuild.py`                                  | 从新权威重建投影，不以旧历史存在为前提                                            |
| `atomic_memory_security.py`、current 索引及候选过滤 | 删除专属安全模块，调用现有公共授权和事务回调；无鉴权允许无 Owner |
| 公共引用模型、SDK 导出、OpenAPI 与 HTTP 生成模型 | 移除旧 Memory citation 类型和字段；历史格式只使用显式无类型 JSON |
| Dream、Work 和回执持久化读取 | 只解析迁移后的新业务或历史格式，不导入旧类型与迁移解码器 |


不改共享授权模型来理解旧 manifest。标准业务层看到的对象统一为 Atomic。

## 11. 验证与验收



### 11.1 启动和离线验证分工

正常启动只检查公共 schema 版本、必要的当前结构和公共表隔离完成条件，不扫描归档集合或 entry 历史，也不自动修复迁移。

离线 `verify` 检查全部新权威和投影。保留期内，显式迁移核验仍可使用归档和旧版本表对账；普通运行和投影重建不依赖这些旧数据。后续归档删除后，验证只检查新模型自身一致性。

投影重建的前置检查只要求新权威有效，不要求待修复的 current 已经完整，否则会阻止修复损坏投影。

### 11.2 验收场景

以下是实施时需要补充或调整的验证，不表示已执行。


| 场景                                         | 必须观察到的结果                                  |
| ------------------------------------------ | ----------------------------------------- |
| 多个 Scope、多集合重名 entry、多版本                   | 身份不串，全部正文版本可精确读取                          |
| active、inactive、compact 历史                 | 按原记录转换状态；不从停用推断合并，compact 不变成可恢复 forgotten |
| Generic Artifact 创建、修订及无操作 Source 的 SDK 写入 | 只补原来存在且能归属的操作 Source；所有依据原本为空时保持为空，不补造 Source 或时间 |
| Handoff、Experience、普通 lineage 的精确 citation | 指向原来的内容版本，即使当前 head 已前进                   |
| Handoff omissions 和跨 Scope 发布副本            | 引用完整，按来源 Scope 映射，发布摘要一致                  |
| Task Outcome 与 recurrence                  | 后续消费不访问旧 Memory，账本重放不重复记录业务事件             |
| 已有 Owner、标签和标准共享授权 | 不扩大权限；标签迁移正确，binding ID、主体、状态和有效期不变 |
| 已过期、已撤销的合法 entry binding 与损坏 binding | 前者按原状态自动迁移；目标不存在、selector 非法或角色非法的记录在预检时列出并阻断 |
| 旧授权创建重试与新 Atomic 授权请求 | 旧 selector 请求 unsupported；旧创建摘要保持历史值，新资源复用旧键产生冲突；原 binding 的 revoke/replace 正常使用 |
| 合法无 Owner 的旧部署 | 保持 Owner 为空，创建、修改、检索、抽取及投影重建与其他制品的无鉴权行为一致 |
| 三个阶段中断后重跑                                  | 不重复创建，不回退新 head；公共行已部分移出时仍可从归档重建对应关系      |
| 旧导入结果已有后续修订或合并                             | 修正历史依据，同时保留后续演进                           |
| 迁移完成后只允许业务访问新表                             | 历史读取、检索、生成、flush、Handoff 和投影重建均不查询归档或旧专用表 |
| 公共表隔离与归档完整性                                | 公共表没有旧 Memory 和旧引用列，两个旧公共外键已解除，无指向归档的新外键；后续清理不影响业务 |
| 集合标签与 entry 标签不同                           | 只迁移 entry 标签，集合标签保留在归档；不扩大原有标签命中范围        |
| 新请求夹带旧引用，包括 no-op 和 omissions              | 在持久化或模型调用前明确拒绝                            |
| 新 ArtifactRef 精确点查、server 五个薄适配入口 | 精确 revision 不跟随 head；适配只调用 Atomic；SDK 不保留旧领域类型 |
| Generic memory、旧 tags/ETag/access selector、旧集合 CAS 和其余旧 API | 全部在执行前 unsupported，不读取旧存储，不接受旧并发前提 |
| 全部旧格式终态 Dream、受影响的历史回执和操作记录 | 空旧字段也完成格式转换；原内容可展示，运行时零旧类型解码，历史数据不能重放 |
| WorkContract、CurrentWorkHandoff 和 Task Outcome | 新格式正常参与原 Work 流程，不因删除旧类型而被当作无效 Source 跳过 |
| Dream 产物与大量无关 Experience、Skill、Candidate 共存 | 按运行记录定位相关候选和结果；转换真实旧证据，不读取无关 proposal 和制品正文 |
| Dream 候选后续编辑、审批及结果继续修订 | 从 Candidate 当前 head 找批准结果，覆盖同一 Artifact 的相关历史版本，保留原状态和内容 |
| 其他入口写入或复制的旧引用 | 通用引用字段检查能发现并转换，不把 Dream 定点结果当作全库核验结论 |
| 待执行 Dream | 受影响任务在维护前处理完；不篡改状态或伪造完成 |
| Task Outcome 的 failure 证据检查 | Atomic 与其他普通 ArtifactRef 一样检查指定对象存在；没有 Atomic 专属状态或权限分支，公共入口鉴权照常执行 |
| 真正的整集合依据 | 先归档引用方、目标、位置及原值，再删除在线关系；不展开成员或 Source，在线不再沿旧集合溯源 |
| 删除整集合引用后违反证据约束的 Handoff/Work 条目或 Candidate | 预检覆盖全部历史版本，列出具体记录、版本和字段位置；无决策时阻断升级，决策文件完整覆盖时按 `replace`/`declare`/`reject` 处理并归档原值；无此类记录时继续，不新增历史条目变体 |
| 保留旧调度身份 | cursor、generation、pending/flush 及已接受任务进度不变，处理器生成 Atomic，不重新消费已有 Source |
| SQLite、seekDB、OceanBase 的失败恢复              | 使用各后端实际 DDL 和事务行为验证，不能用模拟结果替代             |

业务源码验收要求 `MemoryCitation`、`memory_citations` 零命中，范围包含公共模型、server、HTTP 生成模型、Runtime、各制品服务与公共持久化实现。只有登记的离线迁移资源及其冻结格式定义允许保留旧格式名称；这些资源不得被正常启动、API、Worker 或历史查询导入。除名称检查外，检查旧 `kind=memory` 解析分支和按 JSON 形状恢复旧对象的路径均已移除。

数据库验收确认两张公共表的旧引用列不存在，正常 lineage 没有指向旧集合的边。旧字段名可以作为归档或 `historical_data` 中的原始 JSON 键出现，但不对应业务类型或解析规则。上述是实施验收要求，不表示本次文档修改已经实现或运行了这些检查。




### 11.3 规模与可观察性

计划和执行报告至少记录：旧容器数、归档版本数、entry 数、内容版本数、改写记录数、待移出公共行数，以及导致阻断的具体对象。删除集合引用后违反证据约束的合法记录与损坏 binding 分开统计；前者列明全部受影响历史版本及字段位置，后者列明违反的原授权规则。引用处理分开记录读取的行数、字段字节数和实际改写数：

| 工作 | 处理量来源 |
| --- | --- |
| entry 内容和依据导入 | 全部旧 entry 历史及对应 manifest |
| Dream 产物定位和转换 | Dream 运行记录、相关 Candidate 版本、相关 Artifact revision 的引用字段 |
| 通用旧引用核验 | Artifact、Candidate 的引用字段及公共 lineage；缺少引用索引时仍可能遍历全部相关行 |
| Handoff、结构化 Work Source 引用转换 | 含嵌入引用的相关历史正文及已有依赖摘要 |
| 显式历史字段改写 | 全部旧格式终态 Dream，以及受影响的回执和操作记录的 payload |

Dream 定点处理不随无关 Experience、Skill 的总数直接增长，也不触发下游制品正文重写。通用引用核验仍有扫描成本，应单独测量，不能把两项混为一谈。普通 Artifact 的正文、Candidate proposal 不用于通用引用字段检查。

按 Scope、容器和载体主键分页，不将全部 Scope 的全部历史一次装入内存。临时映射从归档集合和旧版本表重建，不建立跨进程恢复文件或永久映射表。

当前向量只覆盖在役 head。历史 revision 不生成向量；复用旧向量必须证明输入和完整 profile 一致，否则重新生成。embedding 在写事务外准备。无向量部署仍建立文本检索投影。

记录最大批次内存、最长事务、embedding 调用数、转换时间，以及归档和新数据在保留期内的并存空间。发布前用目标规模的副本确定批次大小和维护窗口，不预先给出未经测量的吞吐或停服时长。

## 12. 实施前需要收敛的具体事项

1. **统一迁移框架与归档结构。** 落实归档表、两个外键解除、公共旧引用列删除、独立数据任务和就绪检查的执行边界，验证各后端的重试路径。
2. **历史格式与接口契约。** 为 Dream、回执和操作记录落实显式历史格式，完成 schema 与读取接口调整；业务源码零旧类型命中后才能交付。
3. **受影响任务的排空。** 明确停止接收新请求、等待旧 Dream 完成、停止写入者和取得维护锁的步骤。运行记录已经进入终态后再开始转换，不能在修改 schema 的同时继续运行旧 Worker。



上述事项不改变一条 entry 对应一个 Atomic 的模型；它们决定具体部署能否直接执行这次迁移，以及执行前需要处理哪些旧记录。
