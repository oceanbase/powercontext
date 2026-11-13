---
title: PowerContext RFC
description: "何时提交 RFC、提案如何评审，以及到哪里查阅当前实现的公开契约。"
---

# PowerContext RFC

RFC 记录重要的设计提案及其决策。

RFC 描述设计意图，不代表已经发布的行为或实现进度。已实现的公开契约及其可用性以当前源码和
[Python API 参考](https://powercontext.oceanbase.io/zh/modules/)为准。

RFC（request for comments）流程为重大变更提供一致路径，使维护者和贡献者可以在实现开始前形成共识。

许多变更，包括错误修复、文档改进和小规模内部重构，都可以通过常规 GitHub pull request 流程完成评审。

有些变更足够重大，需要先经过设计评审。RFC 流程的目标是让这些决策显式、持久，并且便于未来重新审视。

## 哪些变更需要 RFC？

任何需要大量设计或实现工作的重大变更或新增能力，通常都应该提交 RFC。

示例包括：

- 新的公共 API、集成边界或扩展机制。
- 对持久化格式、交接语义或兼容性保证的变更。
- 移除已经发布的功能。
- 改变核心架构的大规模重构或重组。

是否需要 RFC 的最终判断由项目维护者决定。

如果 pull request 在没有 RFC 的情况下实现了重大功能，维护者可能会要求先提交 RFC 再继续评审。

## 创建 RFC 之前

在打开 RFC 之前，先尝试与维护者和其他贡献者验证问题与设计方向。

有用的准备步骤包括：

- 打开 GitHub issue 描述问题并收集早期反馈。
- 在确定某个实现方向前，先共享备选方案和权衡。
- 让初始范围足够窄，以便评审和实现。

## RFC 流程

- Fork [PowerContext repo](https://github.com/oceanbase/powercontext)，并从 `master` 创建分支。
- 将 [`0000_example.md`](0000_example.md) 复制为 `0000-my-feature.md`，其中 `my-feature` 应具有描述性。
- 打开 pull request 前不要分配 RFC 编号。RFC 编号应与 pull request 编号一致。
- 提交包含 RFC 文档的 pull request，文档位于 `docs/en/rfcs/` 下，并同步维护 `docs/zh/rfcs/` 中的中文翻译。
- pull request 打开后，将 `0000-` 前缀重命名为 pull request 编号。
- 通过常规 pull request 评审建立共识并整合反馈。
- 以追加 commit 的方式修改内容，便于评审者跟踪设计历史。
- 合并后，将 RFC 保留为长期设计记录。
- 如果已经存在专门的实现 issue，可以在 RFC 中将其作为可选的 `Tracking Issue` 引用。

## 公开内容与引用规范

RFC 是长期保留的公开设计记录。它的动机和每一条引用都必须能被组织外部的读者使用，因为 RFC 流程是在公开环境中形成共识。提交 RFC PR 前请逐项检查：

- **不出现外部用户名称。** 不得在正文、头部字段、链接文本、示例或 commit message 中出现外部客户、用户、合作方及其内部项目的名称；需求用中性表述说明。不要只在一个语种脱敏：`docs/en/` 与 `docs/zh/` 的措辞和引用必须一致。
- **不出现内网链接。** 不得链接蚂蚁集团 / 阿里集团内部文档，或任何需要集团 SSO 才能访问的资源，特别是 `yuque.antfin.com/...` 这类链接；其它内网域名、私有文档 ID、内部聊天或工单链接同样禁止。评审者打不开依据，就无法验证声称基于该依据的设计。
- **只引用公开来源。** 引用公开的 GitHub issue、PR、RFC 或上游公开文档。若底层需求尚未公开，应在两个语种的「摘要 / 动机」中概括相关部分，而不是给出链接。
- **头部字段遵循模板。** 只使用 [`0000_example.md`](0000_example.md) 中的字段（`Proposal Name`、`Start Date`、`RFC PR`）以及上文允许的可选 `Tracking Issue`。不要为需求来源或「业务目录」自创头部字段；补充溯源信息放在正文或所链接的公开 issue 中。

真实的内网 URL 会被自动拦截：`make check` 通过 prek 运行 `scripts/check_public_content.py`，也可以直接对全部已跟踪文件执行同一检查（上面作为反例引用的裸域名不是链接）：

```bash
uv run python scripts/check_public_content.py --all
```

检查脚本只匹配 `scripts/check_public_content.py::INTERNAL_HOST_SUFFIXES` 中固定的内网主机后缀清单（`antfin.com`、`alibaba-inc.com`、`alipay-inc.com`、`aliyun-inc.com`），因此 `*.internal.example`、`host.docker.internal` 这类合法的本地示例不会误报。其它内网域名、私有文档 ID、公开域名上的 SSO 私有空间无法机械识别；这些以及所有外部名称仍需人工通读两个语种。

以上规则适用于整个仓库，参见 [CONTRIBUTING.md](https://github.com/oceanbase/powercontext/blob/master/CONTRIBUTING.md) 的 "Write Documentation" 章节。

## 实现 RFC

RFC 合并不代表已经确定实现优先级、负责人或完成情况。

鼓励 RFC 作者实现该设计，但这不是强制要求。

RFC 流程不要求 tracking issue。不要仅为了推进 RFC 流程或记录 RFC 进度而创建 tracking issue。

如果设计需要实质性变更，请提交后续 pull request 或新的 RFC，而不是静默改变实现契约。

## 建议

- 写出足够细节，使 RFC 作者之外的人也能实现该设计。
- 明确指出缺点、替代方案和兼容性风险。
- 当实现细节会影响公共行为时，保持具体。
- 将未决问题视为设计的一部分，而不是评审遗留项。
