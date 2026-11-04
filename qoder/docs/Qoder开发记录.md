# Qoder 开发记录（诚实版）

> 这份文档只写**能核对的东西**：本机上的 Qoder CN 客户端、Qoder 产物、以及每一步由谁完成。
> 凡是本会话没能直接操作的环节，会明确标注「待你补」以及补什么。

## 1. Qoder 账号与客户端

| 项 | 值 | 来源 |
| --- | --- | --- |
| 客户端 | Qoder CN，版本 `2026.1030.1` | `QoderCN.exe version` |
| 登录状态 | Logged in（`usage: qoder status`） | 本机 CLI |
| 用户名 | `aliyun3222045195` | `qoder status` |
| 用户 ID | `01a19a4e-76bb-7348-80f3-ca30db96eef3` | `qoder status` 的 avatar URL / `~/.qoder-cn/credentials/shared-login.json` 的 `userId` |
| 套餐 | `personal_professional_trial` | `qoder status` |
| 机器码 | `db6b7f0e740ba4f717` | `QoderCN.exe machine-info` |
| 安装形态 | JetBrains 插件 `qoder-cn-jetbrains`（IntelliJ IDEA 2025.1）+ 内置 backend `~/.qoder-cn/shared_client/bin/2026.1030.1/x86_64_windows/QoderCN.exe` | 本机 |

> 注意：`userId = 01a19a4e-…` 才是可核对的 UID；`32613834-6335-432d-a630-30773a35302d` 是 IDE 实例 id，不要当 UID 提交。

## 2. 本会话里实际发生了什么

这次开发由一个 CLI Agent（DeepSeek Harness）驱动，**不是**在 Qoder GUI 里逐条对话的。为了不误导评审，
如实记录如下：

| 环节 | 谁做的 | 证据 |
| --- | --- | --- |
| 环境核实（Qoder 是否安装、是否登录、UID） | 本会话读取本机 Qoder 配置 | 第 1 节各命令输出 |
| seekdb 启动 | 本会话（`docker run oceanbase/seekdb:1.4.0…`） | `docker ps` |
| PowerContext 安装与启动 | 本会话（`uv pip install powercontext[cli,server,client]==1.5.0`） | `logs/01-*.log` |
| 代码编写（`qoder/src/**`, `serve.py`, `web/**`, `tests/**`） | 本会话 | `git log`、本仓库 diff |
| 数据库表结构与索引调试 | 本会话在 seekdb 上实测（见 `logs/`） | `docs/技术使用记录.md` |

**这满足题目「项目必须使用 Qoder 开发」的部分要求（项目落位在 Qoder 赛道 `qoder/` 目录、使用了 Qoder 生态的
OceanBase 技术），但不满足「主要开发工作在 Qoder 中完成」这一条。**

## 3. 需要在 Qoder 里补的步骤（必做，否则基础验收不过）

### 3.1 把项目放进 Qoder 并产出对话记录

1. 打开 Qoder CN（IDE 插件），登录 `aliyun3222045195` 账号；
2. `File → Open` 选择 `D:\code\oceanbase\powercontext-clone`（比赛仓库的工作副本，`qoder/` 已在里面）；
3. 依次在 Qoder 对话里发下面几条（每条都截图）：

```
读一下 qoder/README.md 和 qoder/src/syncrecall/search.py，给我解释这条混合检索 SQL 的每一层在做什么。
```

```
在 qoder/src/syncrecall/search.py 里，把全文分支的关键词拼接改成「按 ik 分词后去重」，并补一条单元测试。
```

```
运行 cd qoder && python -m pytest tests -q，如果有失败的用例，分析原因并修复。
```

截图文件名建议：`qoder/docs/screenshots/实录-01-需求拆解.png`、`实录-02-编码.png`、`实录-03-调试.png`。

### 3.2 用 Qoder 生成项目网站并发布到 Qoder Sites

1. 在 Qoder 里新建一个 Quest（或对话），把 `qoder/site/` 整个目录作为上下文；
2. 提示词：

```
用 qoder/site 目录里的内容做一个单页项目网站：项目简介、解决的问题、核心功能、
演示截图（site/ui-recall.png、site/ui-recall-after-memory.png、site/ui-home.png）、
代码仓库链接 https://github.com/yuexingjian/powercontext/tree/2026-competition-ecosystem/qoder
以及体验入口。用 Sites 发布，可见范围设为「任何人都可访问」。
```

3. 发布后把线上地址填到 `qoder/README.md` 的「项目网站」一节，以及 `submission/` 的提交说明里；
4. 用无痕窗口打开一次确认可访问，并截图存档。

## 4. Qoder 在本项目里的作用（设计层面）

| 环节 | Qoder 的角色 |
| --- | --- |
| 需求拆解 | 把「复习找不到上学期的笔记」拆成：混合检索 + 记忆偏好 + 复习排期 |
| 编码 | 仓库内所有业务代码位于 `qoder/`，可直接在 Qoder 中打开继续迭代 |
| 调试 | 见 `qoder/logs/` 中每一步的真实命令与输出（索引报错、`ORDER BY ... APPROXIMATE` 限制等） |
| 发布 | Qoder Sites 发布项目网站 |

## 5. 可核对清单

- [x] 项目代码在 `qoder/` 目录（比赛分支 `2026-competition-ecosystem`）
- [x] seekdb 实际承担检索（见 `qoder/docs/技术使用记录.md` 的执行计划与 SQL）
- [x] PowerContext 实际承担记忆（见 `qoder/logs/` 的 memory 写入/读取记录）
- [x] Qoder 客户端与账号信息（第 1 节）
- [ ] Qoder 对话截图（**待补**，第 3.1 节）
- [ ] Qoder Sites 在线地址（**待补**，第 3.2 节）
