# SyncRecall — 把读过的资料变成能被问出来的记忆

> Qoder × OceanBase 2026 生态赛道参赛作品 · 目录 `qoder/`
> 基于比赛分支 [`2026-competition-ecosystem`](https://github.com/oceanbase/powercontext/tree/2026-competition-ecosystem)

期末复习时最花时间的不是「学」，而是**找回**：上次学到哪、哪段笔记讲过这个、当时是怎么想通的。
SyncRecall 把散在文档、PDF、群聊、会议纪要里的资料放进 **seekdb** 做混合检索，用 **PowerContext**
记住你的检索偏好、当前聚焦和薄弱点，并把读过的内容自动变成下次要答的复习题。

```
      你问一句                  PowerContext 记得             seekdb 一条 SQL 完成
  ┌──────────────┐          ┌──────────────────┐        ┌──────────────────────────────┐
  │ 问题 + 本地向量 │ ───────► │ 检索偏好 / 聚焦 / 薄弱点 │ ─────► │ 结构化过滤 → 全文分支 + 向量分支 │
  └──────────────┘          │   （写入 Scope 长期保存） │        │   → 库内加权 RRF → 排序结果    │
                            └──────────────────┘        └──────────────────────────────┘
                                     ▲                                │
                                     └──── 会话交接 / 答错的主题 ◄─────┘
```

## 1. 它解决的问题

| 痛点 | 本项目的做法 |
| --- | --- |
| 资料散在四个地方，关键词只对得上一半 | seekdb 全文（`ik` 分词）+ 向量（HNSW）双路检索，在**一条 SQL 内**用 RRF 融合 |
| 「只在分布式系统里找」「多看论文」每次都要重设 | 偏好写进 PowerContext，成为下一次检索的过滤条件与融合权重 |
| 答错的地方从来不参与排期 | 答题评分写入 seekdb 的 SM-2 排期；答错的主题写回 PowerContext，下一轮优先出现 |
| 不知道系统到底干了什么 | 界面可展开 seekdb 实际执行的 SQL 与 PowerContext 的读写记录；全部动作落 `event_log` 表 |

## 2. 快速开始（约 5 分钟）

### 2.0 环境要求

| 项 | 版本（实测） |
| --- | --- |
| Python | 3.10.4（3.9+ 可用） |
| Docker | Docker Desktop 4.82 / Engine 29.6 |
| seekdb | 镜像 `oceanbase/seekdb:1.4.0-100000172026082615`（`5.7.25-OceanBase seekdb-v1.4.0.0`） |
| PowerContext | `powercontext[cli,server]==1.5.0` |
| 嵌入模型 | `sentence-transformers` + `paraphrase-multilingual-MiniLM-L12-v2`（可选，见 2.3） |

### 2.1 启动 seekdb

```bash
docker run -d --name seekdb-kb -p 2881:2881 -e MODE=mini \
  oceanbase/seekdb:1.4.0-100000172026082615
```

容器起来大约需要 10–30 秒；`mysql -h127.0.0.1 -P2881 -uroot` 能连上即可。

### 2.2 启动 PowerContext Server

```bash
# 建议在独立的 venv 里装，避免污染系统 Python
python -m venv .venv-powercontext
. .venv-powercontext/bin/activate          # Windows: .venv-powercontext\Scripts\activate
pip install "powercontext[cli,server]==1.5.0"

powercontext config init --language zh --output powercontext.env   # 存储选 seekdb
powercontext server run --env-file powercontext.env                # 默认 http://127.0.0.1:18080
```

`powercontext.env` 只需要这四项就能跑起来（其余用默认值）：

```ini
POWERCONTEXT_SERVER_HTTP_HOST=127.0.0.1
POWERCONTEXT_SERVER_HTTP_PORT=18080
POWERCONTEXT_SERVER_ACCESS_MODE=disabled
POWERCONTEXT_SERVER_METRICS_ENABLED=false
```

> **关于 PowerContext 的存储**：向导里选「嵌入式 seekdb」时，PowerContext 会把上下文
> （Scope / Memory / Handoff）存在自己的 seekdb 实例里，本项目的资料与复习排期存在
> 2.1 启动的 seekdb 服务端实例里 —— 两边是同一个产品的两个实例，互不干扰。
> 嵌入式 profile 依赖 `pylibseekdb`，目前只发布 Linux / macOS 包；Windows 上请按向导改用 SQLite，
> 或直接在 WSL2 / Linux 上跑（见 `docs/技术使用记录.md` 的「环境说明」）。

### 2.3 启动应用

```bash
cd qoder
pip install -r requirements.txt          # fastapi / uvicorn / pymysql
pip install -e .                          # 可选；装好后任意目录都能用 `syncrecall` 命令
pip install -e ".[semantic]"              # 可选：装上真实语义模型（首次运行下载约 470 MB）

python -m syncrecall seed                 # 建表 + 导入 21 条示例资料 + 60 道复习题
python -m uvicorn serve:app --host 127.0.0.1 --port 8788
```

打开 <http://127.0.0.1:8788/>。

> `python -m syncrecall` 需要包可导入，即在 `qoder/` 下执行过 `pip install -e .`，
> 或临时设置 `PYTHONPATH=src`。**`scripts/` 下的脚本与 `serve.py` 已经自己处理了路径**，
> 不装也能直接跑：`python scripts/demo_recall.py "问题"`、`python verify.py`。

一条命令看完整故事（自己建一个临时 Scope，不需要装包）：

```bash
cd qoder && python verify.py
```

它会：导入示例数据 → 同一个问题检索两次（中间往 PowerContext 写一条偏好）→ 打印两次排名对比、
记忆读写记录和 seekdb 执行计划。

> 没有装 `sentence-transformers` 也能完整跑通：此时嵌入方式自动退化为确定性的 n-gram 哈希
> （不联网、结果可复现），向量分支仍然走 HNSW 索引，只是「语义相近」的能力弱一些。
> 界面右上角与启动日志都会显示当前使用的嵌入方式。

### 2.4 环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `SR_DB_HOST` / `SR_DB_PORT` | `127.0.0.1` / `2881` | seekdb 地址 |
| `SR_DB_USER` / `SR_DB_PASSWORD` | `root` / 空 | seekdb 账号（示例使用 root 空密码，生产请改） |
| `SR_DB_NAME` | `syncrecall` | 应用数据库名，首次启动自动创建 |
| `SR_POWERCONTEXT_URL` | `http://127.0.0.1:18080` | PowerContext Server 地址 |
| `SR_EMBEDDING_PROVIDER` | `auto` | `auto` / `sentence-transformers` / `hash` |
| `SR_EMBEDDING_MODEL` | `paraphrase-multilingual-MiniLM-L12-v2` | 更换模型时同时改 `SR_EMBEDDING_DIM` 并重新 `seed` |
| `SR_EMBEDDING_DIM` | `384` | 向量维度，需与模型一致 |
| `SR_HOST` / `SR_PORT` | `127.0.0.1` / `8788` | 应用监听地址 |

## 3. 目录结构

```
qoder/
├── src/syncrecall/
│   ├── store.py       # seekdb：建表、索引、写入、审计日志
│   ├── search.py      # 一条 SQL 的混合检索（全文 + 向量 + 结构化过滤 + RRF）
│   ├── memory.py      # PowerContext 边界：偏好 / 聚焦 / 薄弱点 / 会话交接
│   ├── service.py     # 业务编排：recall、review_plan、grade_card、seed
│   ├── embedding.py   # 本地嵌入（sentence-transformers / 哈希回退）
│   ├── config.py      # 只有环境变量，不含任何凭据
│   └── cli.py         # python -m syncrecall seed|recall|review|memory|health
├── serve.py           # FastAPI 接口 + 静态页面
├── web/               # 前端（原生 JS，无构建步骤）
├── tests/             # 22 个测试：单元 + 真实 seekdb / PowerContext 集成
├── data/seed.json     # 示例资料：笔记 / 论文 / 群聊 / 会议纪要（虚构数据）
├── scripts/           # 造数、截图、取证、执行计划等辅助脚本
├── docs/              # 运行说明、验证样例、技术使用记录、Qoder 开发记录、证据
├── logs/              # 逐步复现的命令与输出
├── site/              # Qoder Sites 项目网站（发布用）
├── requirements.txt
└── README.md
```

## 4. 主要接口

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| `GET` | `/api/health` | seekdb 版本、PowerContext 就绪状态、当前嵌入方式 |
| `GET` | `/api/bootstrap?user=` | 课程/来源类型清单、记忆快照、示例问题 |
| `POST` | `/api/recall` | `{user, question, limit?}` → 命中列表 + 引用 + 实际 SQL + 记忆事件 |
| `GET` | `/api/review?user=&course=` | 复习计划（seekdb 排期 + PowerContext 薄弱点） |
| `POST` | `/api/review/grade` | `{user, card_id, grade}` → 下次复习时间 + 写入的薄弱点 |
| `GET`/`POST` | `/api/profile` | 读取 / 写入 PowerContext 中的检索偏好 |
| `POST` | `/api/focus` | 写入「当前聚焦」 |
| `GET` | `/api/events?user=&limit=` | seekdb `event_log` 审计记录 |

命令行等价入口：

```bash
python -m syncrecall health
python -m syncrecall seed --reset
python -m syncrecall recall "Raft 怎么保证不丢已提交的日志"
python -m syncrecall review --course 分布式系统
python -m syncrecall memory
```

## 5. 验证样例

一组可按步骤执行的样例（含初始数据、输入、预期结果、以及「指定技术在哪一步发挥作用」的记录）
见 [`docs/验证样例.md`](docs/验证样例.md)。最省事的一条：

```bash
cd qoder
python verify.py
```

它建一个临时 Scope，把同一个问题「Raft 怎么保证不丢已提交的日志」先在不设偏好的情况下检索一次，
再往 PowerContext 写入「只看分布式系统的笔记与论文」后检索一次，打印两次排名、记忆读写记录
与 seekdb 执行计划。实测输出见 [`logs/09-verify.log`](logs/09-verify.log)：

| | 命中范围 |
| --- | --- |
| 第一次 | `分布式系统` ×5 + **`数据库系统` ×1**（redo/undo 笔记），首位是《组会纪要》 |
| 第二次 | **全部 `分布式系统`**，只含笔记与论文，首位是《Raft 日志复制与提交规则》，Raft 原始论文进入结果 |

前后对比的原始数据在 [`docs/evidence/before-after.json`](docs/evidence/before-after.json)。

## 6. 运行测试

```bash
cd qoder
python -m pytest tests -q          # 22 passed
```

- 单元测试（12 个）不需要任何外部服务；
- 集成测试会检测 seekdb / PowerContext 是否可达，不可达时**跳过并给出原因**，不会报错。

## 7. 已知限制

1. **示例语料小**：21 条资料 / 60 道题，只够把流程跑通，不足以做有统计意义的检索评测。
2. **默认嵌入是哈希回退**：没装 `sentence-transformers` 时，向量分支只有字面相似能力；
   真实语义效果需要安装该依赖后重新 `python -m syncrecall seed`。
3. **最新偏好生效采用时间排序**：PowerContext 的 `memory/search` 按相关度返回，而本应用需要
   「最后一次写入的偏好生效」，因此读取时额外按 Artifact 的 `created_at` 排序（见
   `memory.py::_all_of_kind`）。旧偏好不会被删除，仍在 PowerContext 里可查。
4. **单用户本地部署**：未做账号体系，`user` 只是一个 Scope 名；生产环境需要自行接入身份认证。
5. **Windows 上的 PowerContext 存储**：嵌入式 seekdb 需要 Linux / macOS，Windows 请用 SQLite 或 WSL2。

## 8. 材料索引

**项目网站（Qoder Sites，任何人都可访问）：<https://syncrecall-zrr0ailg6io.qoder.zone>**

| 材料 | 位置 |
| --- | --- |
| 项目网站（线上） | <https://syncrecall-zrr0ailg6io.qoder.zone> |
| 项目网站（源码，发布用） | [`site/`](site/) |
| 运行说明 | 本文 |
| 验证样例 | [`docs/验证样例.md`](docs/验证样例.md) |
| 技术使用记录（seekdb / PowerContext 的分工与实测） | [`docs/技术使用记录.md`](docs/技术使用记录.md) |
| 一页技术说明 | [`docs/一页技术说明.md`](docs/一页技术说明.md) |
| Qoder 开发记录（含账号 UID） | [`docs/Qoder开发记录.md`](docs/Qoder开发记录.md) |
| 逐条命令与输出 | [`logs/`](logs/) |
| 界面与站点截图 | [`docs/screenshots/`](docs/screenshots/) |
