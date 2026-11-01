# 用户自定义 Artifact 插件示例

用户把插件部署到统一发现目录。PowerContext Server 启动时校验新插件并保存定义，首次加载为 `disabled`；管理员启用后，Supervisor 按清单参数派发 Worker。Worker 加载 pipeline，框架保存生成结果，应用通过公开 API 读取。

| 模板目录 | family / binding | 生成结果 | 业务配置 |
| --- | --- | --- | --- |
| `incident_report` | `incident-report` / `incident-reports` | 每条 Source 一份工单报告 | `title_prefix` |
| `ticket_summary` | `ticket-summary` / `ticket-summaries` | 每个 Source 窗口一份文本摘要 | `language` |

本示例使用文件型 SQLite、global Supervisor、Server 的 all 角色、Worker 内加载 Python 和声明式文本检索，不依赖 LLM。内存型 SQLite 可以加载声明，但启用生成会返回 `422`，因为 Worker 子进程无法共享 Server 的内存数据库。

## 1. 配置并启动 Server

在仓库根目录执行：

```bash
uv sync --extra server --extra cli
export POWERCONTEXT_HOME="$PWD/.powercontext/artifact-plugin-example"
export POWERCONTEXT_SERVER_DATABASE='{"kind":"sqlite","url":"sqlite+aiosqlite:///.powercontext/artifact-plugin-example.db"}'
export POWERCONTEXT_SERVER_RUNTIME='{"artifact_processing_families":[]}'
export POWERCONTEXT_SERVER_AUTH_ENABLED=true
export POWERCONTEXT_SERVER_AUTH_TOKEN=local-example-admin
export POWERCONTEXT_SERVER_ACCESS_MODE=enforced
export POWERCONTEXT_SERVER_ARTIFACT_PLUGINS_DIR="$POWERCONTEXT_HOME/plugins"
mkdir -p "$POWERCONTEXT_SERVER_ARTIFACT_PLUGINS_DIR"
uv run powercontext server run --no-env-file --host 127.0.0.1 --port 8000
```

Server 的 `.env` / envfile 保存数据库、监听、鉴权、共用模型和统一插件目录等全局设置。`POWERCONTEXT_SERVER_ARTIFACT_PLUGINS_DIR` 沿用现有加载规则：环境变量优先于 envfile。各插件的 Scope、批次、调度和业务参数只保存在各自的 `plugin.yml`。

以下命令在仓库根目录的另一个终端运行，需要 `curl` 和 `jq`。首次使用先创建 Scope；已有 Scope 的部署可以在启动前准备好插件目录。

```bash
export PC_URL=http://127.0.0.1:8000
export PC_AUTH='Authorization: Bearer local-example-admin'
export PC_PLUGIN_ROOT="$PWD/.powercontext/artifact-plugin-example/plugins"
export PC_VERSION_ROOT="$PWD/.powercontext/artifact-plugin-example/versions"
export PC_REPORT_SCOPE=$(curl -fsS -H "$PC_AUTH" -H 'Content-Type: application/json' \
  "$PC_URL/v1/scopes" -d '{"title":"Reports","summary":"Incident reports","idempotency_key":"artifact-reports-example"}' \
  | jq -r .scope_id)
export PC_SUMMARY_SCOPE=$(curl -fsS -H "$PC_AUTH" -H 'Content-Type: application/json' \
  "$PC_URL/v1/scopes" -d '{"title":"Summaries","summary":"Ticket summaries","idempotency_key":"artifact-summaries-example"}' \
  | jq -r .scope_id)
```

## 2. 部署插件清单和代码

每个插件目录有一份完整清单。报告插件示例如下，显式填写的 `scope_ids` 必须对应实际存在的 Scope：

```yaml
id: example-incident-report
version: "1"
entry: ./plugin.py:create_pipeline
binding_name: incident-reports
scope_ids: [team-a]
max_sources: 20
processing:
  interval_seconds: 1
  max_workers: 1
  timeout_seconds: 60
config:
  title_prefix: 工单快照报告
```

`entry` 相对清单所在目录解析。统一目录只发现直接子目录，目录项名称必须等于 `binding_name`。版本目录放在发现目录之外，通过符号链接提供当前入口；Server 保存解析后的实际目录，切换链接不会改变已有 Worker 使用的代码。

Scope、窗口上限、发现间隔、并行 Worker 数和 Worker 超时只作用于对应 binding。数值必须为正数，批次和 Worker 数必须为整数。`config` 按插件的 `Config` 模型校验后交给 pipeline。清单顶层禁止 `enable` / `enabled` 字段，无论值为 true 还是 false；运行状态由管理 API 控制。含这些字段的清单会被拒绝，reload 返回 `422` 并保留已有有效定义和状态。

`scope_ids` 可省略：框架动态发现已有输入积压和后续新增的 Scope，并按后台身份校验访问权限。显式填写非空名单时，该 binding 只处理名单内的已有 Scope。处理范围不授予权限。空数组 `scope_ids: []` 会被拒绝。

摘要插件使用自己的 Scope、`max_sources=4`、5 秒间隔、2 个 Worker、90 秒超时和 `language` 配置。插件通过安装包中的 `powercontext.artifact_plugins` 声明 schema、输入契约和可选 FTS 能力。

```bash
mkdir -p "$PC_PLUGIN_ROOT" "$PC_VERSION_ROOT"
cp -R examples/artifact_plugins/incident_report "$PC_VERSION_ROOT/incident-report-v1"
cp -R examples/artifact_plugins/ticket_summary "$PC_VERSION_ROOT/ticket-summary-v1"
uv run python - <<'PY'
import os
from pathlib import Path
import yaml

root = Path(os.environ["PC_VERSION_ROOT"])
for name, scope in (
    ("incident-report-v1", os.environ["PC_REPORT_SCOPE"]),
    ("ticket-summary-v1", os.environ["PC_SUMMARY_SCOPE"]),
):
    path = root / name / "plugin.yml"
    manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
    manifest["scope_ids"] = [scope]
    path.write_text(yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False), encoding="utf-8")
PY
ln -s "$PC_VERSION_ROOT/incident-report-v1" "$PC_PLUGIN_ROOT/incident-reports"
ln -s "$PC_VERSION_ROOT/ticket-summary-v1" "$PC_PLUGIN_ROOT/ticket-summaries"
```

Artifact 插件通过输入映射选择每种 Source 的 Projection。两个示例均消费主线已有的 `content` Source：

```python
inputs = InputContract(projections={"content": TEXT_EVIDENCE_PROJECTION_KEY})
```

映射必须非空，每种类型选择一个合法的 ProjectionKey。`GenerationSource` 包含 `ref`、`projection` 和完整 JSON `value`；对象、数组、嵌套字段、数字、布尔值和 null 保持原有结构。示例调用 `TextEvidence.model_validate(source.value).content` 读取文本。

一个 family 可消费多个 Source 类型，一个类型也可供多个 family 消费。关联由上述输入声明和输出 family 决定，与两端插件 ID 或 binding 名称无关。`max_sources` 限制 journal 窗口条目数；未声明类型被过滤，batch 不保证所有类型齐全，不等待凑齐或自动配对。draft 只列出实际使用的精确 SourceRef。

对于外部 Source，Worker 读取已保存的 Projection，不加载 Source 插件代码，不请求外部最新数据。已接受类型缺少指定名称或版本时整个窗口失败，游标不推进，也不使用其他投影代替。输入预算按完整批次 JSON 证据计量，当前上限为 1 MiB；超限不截断或部分提交，可通过新目录和 reload 调整窗口大小。

## 3. 加载并启用

启动扫描会加载首次发现的插件。Server 已运行时，新增插件需要显式 reload；请求只携带 `expected_generation`，首次为 0：

```bash
for binding in incident-reports ticket-summaries; do
  curl -fsS -H "$PC_AUTH" -H 'Content-Type: application/json' \
    "$PC_URL/v1/admin/artifact-plugins/$binding/reload" -d '{"expected_generation":0}'
done
curl -fsS -H "$PC_AUTH" "$PC_URL/v1/admin/artifact-plugins" | jq
curl -fsS -X POST -H "$PC_AUTH" "$PC_URL/v1/admin/artifact-plugins/enable-all" | jq
```

reload 返回 `202` 和 `Location`。首次加载只校验入口、Config、输入输出声明、Scope 与身份，保存完整定义和读取能力；不调用生成工厂或生成方法。查询状态到 `disabled` 后即可启用。

enable-all 无需请求体，只处理请求开始时已加载且 disabled 的插件，不扫描目录。逐插件返回 `accepted`、`skipped` 或 `failed`、安全错误码和 `status_url`。一个失败不回滚其他成功项；已启用、卸载、failed 或转换中的插件跳过。通过状态 URL 查询到 `phase=enabled` 才表示异步装配完成。首次加载失败不产生注册记录，启动日志报告该目录的失败，其他合法插件继续加载。

## 4. 写入 Source 并读取结果

```bash
for scope in "$PC_REPORT_SCOPE" "$PC_SUMMARY_SCOPE"; do
  jq -n --arg scope "$scope" \
    '{scope_id:$scope,source_id:"ticket-1",content:"Outage: database recovered after retry."}' \
    | curl -fsS -H "$PC_AUTH" -H 'Content-Type: application/json' \
      "$PC_URL/v1/sources/content" --data-binary @-
done
curl -fsS -H "$PC_AUTH" "$PC_URL/v1/scopes/$PC_REPORT_SCOPE/artifacts/incident-report" | jq
curl -fsS -H "$PC_AUTH" "$PC_URL/v1/scopes/$PC_SUMMARY_SCOPE/artifacts/ticket-summary" | jq
curl -fsS -H "$PC_AUTH" -H 'Content-Type: application/json' \
  "$PC_URL/v1/scopes/$PC_REPORT_SCOPE/artifacts/incident-report/search" -d '{"query":"Outage"}' | jq
export PC_ARTIFACT=$(curl -fsS -H "$PC_AUTH" \
  "$PC_URL/v1/scopes/$PC_REPORT_SCOPE/artifacts/incident-report" | jq -r '.items[0].artifact_id')
curl -fsS -H "$PC_AUTH" "$PC_URL/v1/scopes/$PC_REPORT_SCOPE/artifacts/incident-report/$PC_ARTIFACT" | jq
```

生成异步执行，尚无产物时 list / search 可返回空结果；已有产物但首次索引尚未就绪时，search 返回 `503`，稍后重试。list 返回 Artifact 身份，get 返回内容、schema 版本和精确 lineage。启用时从持久游标消费历史与停用期间积压；重复写入相同 Source 不产生重复修订，生成失败不推进游标或提交部分结果。

两个示例通过 `ArtifactFamily.search` 和 `TextIndex` 声明检索字段。文本检索支持中文、英文和混合文本，只匹配当前 revision 对应 schema 声明的字段。统一 `/search` 返回 `results`，包含完整 `content`、精确 revision 和 lineage；`include_scores` 可附带评分。

Server 根据持久声明恢复索引，不要求生成插件启用或代码目录存在。首次构建尚未就绪时返回 `503`；已发布索引随写入同步维护。检索表由统一数据库初始化创建，无需单独安装。

向量、多片段及 hybrid 通过 `ArtifactSearchDeclaration`、`VectorIndex`（`source="text"` 或 `source="content"`） 声明。OceanBase/seekDB 热加载新向量 profile 后，需要重新初始化数据库（例如重启 Server）以创建对应空间。

## 5. 启停、更新、卸载与恢复

单插件管理使用最新 GET 返回的 `binding_generation`，过期代次返回 `409`：

```bash
export PC_GENERATION=$(curl -fsS -H "$PC_AUTH" \
  "$PC_URL/v1/admin/artifact-plugins/incident-reports" | jq -r .binding_generation)
curl -fsS -H "$PC_AUTH" -H 'Content-Type: application/json' \
  "$PC_URL/v1/admin/artifact-plugins/incident-reports/disable" \
  -d "{\"expected_generation\":$PC_GENERATION}"
```

查询到 `disabled` 后再读取新代次，可调用 `enable` 或 `unload`。启停不修改清单。卸载回收 Worker 并移除执行定义，保留身份、代次、schema、读取能力、消费进度和已有结果；确认 `unloaded` 后可删除专属版本目录，再次加载需 reload。

更新代码或配置时准备完整的新版本目录，再切换发现入口并调用 reload：

```bash
cp -R "$PC_VERSION_ROOT/incident-report-v1" "$PC_VERSION_ROOT/incident-report-v2"
uv run python - <<'PY'
import os
from pathlib import Path
import yaml

path = Path(os.environ["PC_VERSION_ROOT"]) / "incident-report-v2/plugin.yml"
manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
manifest["version"] = "2"
manifest["config"]["title_prefix"] = "新版工单报告"
path.write_text(yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False), encoding="utf-8")
PY
ln -s "$PC_VERSION_ROOT/incident-report-v2" "$PC_VERSION_ROOT/incident-reports-next"
mv -Tf "$PC_VERSION_ROOT/incident-reports-next" "$PC_PLUGIN_ROOT/incident-reports"
export PC_GENERATION=$(curl -fsS -H "$PC_AUTH" \
  "$PC_URL/v1/admin/artifact-plugins/incident-reports" | jq -r .binding_generation)
curl -fsS -H "$PC_AUTH" -H 'Content-Type: application/json' \
  "$PC_URL/v1/admin/artifact-plugins/incident-reports/reload" \
  -d "{\"expected_generation\":$PC_GENERATION}"
```

候选校验失败保留原定义和状态。接受更新后撤销旧代次提交资格，回收旧 Worker，再装配新定义；确认完成前保留旧目录。reload 更新正常插件保留原启停状态；卸载或 failed 后加载为 disabled，需要再次启用。同一 `(family, schema_version)` 的内容 schema 不可变。已有检索 family 的 schema 身份、索引定义和版本冻结；无检索 family 可以演进内容 schema，但不能补加检索。允许调整 `modes`、`default_mode`、`defaults`、`candidate_limit`、`rrf_k`、`text_weight`、`vector_weight` 查询策略，更新不会触发索引重建、停搜或重新编码。

Server 重启恢复持久快照与已保存的启停、卸载状态，并继续未完成转换。扫描只发现新插件，不覆盖已有配置或重新加载卸载记录；failed 等待管理员 reload 修复。恢复 enabled 会更新代次，管理操作始终先 GET。无需每次重启调用 enable-all，结果读取不依赖插件进程。

## 6. 通用检索与精确证据读取

上层应用 / Agent 通过公开的 Artifact get / list / 精确 revision 和 FTS 接口获取数据，自行理解问题、组合证据并计算距离、次数或排名。PowerContext 提供存储、生成、版本、鉴权、检索和精确证据读取，不执行插件业务查询回调。

生成插件通过 `GenerationContext` 使用同一套读取与检索语义：

| 接口 | 语义 |
| --- | --- |
| `read_latest(family, artifact_id)` | 读取当前 revision，缺失时返回 `None`；独立调用不形成跨 family 快照 |
| `read_revision(ref)` | 读取精确 revision，支持已被新 head 替代的历史内容；缺失时报错 |
| `list_artifacts(family, limit=50, cursor=None)` | 分页返回可见 active head 的引用；每页上限 100，游标只在本次生成上下文内使用 |
| `search_artifacts(family, query=None, **parameters)` | 经共享 ArtifactSearch 返回完整 Artifact，支持已声明模式和索引；limit 默认 10，上限 200 |

生成默认可读自己的输出 family；需要跨 family 时，使用 `InputContract(..., artifact_families=(...))` 声明额外范围。所有读取固定在当前 Scope，并受 Worker 身份权限约束。get / list 支持已注册 Artifact family；内置 family 的快照没有自定义 `schema_version` 时返回 `None`。生成 Worker 与 HTTP / SDK 遵守相同的检索语义；无检索能力的已知 family 明确返回不支持。

list / search 不自动登记为生成证据。search 返回完整正文；需要用于 draft 的 lineage 或更新基准时，插件仍须显式调用 read_revision 读取所选精确版本，登记实际使用的证据。完整枚举应遍历分页 list 或持久清单，不能用 top-k 表示完整集合。历史读取先固定发布根，再沿清单读取精确引用；当前 FTS 不包含旧 revision，不能据此排除历史对象。

统一 HTTP search 复用共享检索服务。插件停用或卸载后，已保存内容的 get / list / 声明式检索继续可用。普通读取与检索不推进 Source 消费进度。

## 端到端验收

```bash
uv run pytest tests/e2e/test_artifact_plugins.py \
  tests/e2e/test_artifact_plugin_search.py -q
```

测试启动安装包提供的 `powercontext server run`，通过真实 envfile / 环境变量、部署目录和 HTTP 完成加载、启用、消费与查询。覆盖首次加载不生成、多插件独立配置、批量部分失败和并发冲突、reload、单插件启停 / 卸载、重启与失败恢复。

测试只使用产品的公开路径。
