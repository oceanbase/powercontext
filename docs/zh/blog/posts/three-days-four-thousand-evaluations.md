---
slug: three-days-four-thousand-evaluations
title: 3 天 4000 次测评背后的技术解读
description: 数据库大赛如何用 Harbor 组织赛题、公开自测和 Agent 试做，并处理正式判题中的环境复用、资源调度与故障恢复。
cover: assets/blog/three-days-four-thousand-evaluations/cover.png
cover_alt: 提交记录经过有界队列与多个独立验收环境，汇入持久化的结果与回传记录。
date: 2026-11-05
updated: 2026-11-11
authors:
  - chojan-shang
categories:
  - 工程实践
draft: true
pin: false
---

[第六届 OceanBase 数据库大赛](https://open.oceanbase.com/competition) 开赛的前三天，PowerContext 的判题服务就处理了超过 4000 次判题。这组赛题共有 11 题、240 分，覆盖数据采集、记忆状态、检索和上下文构建。

我们为比赛单独搭建了判题服务，由 [Harbor](https://harborframework.com/) 管理执行环境、运行测试并保存结果。赛前，我们让 Agent 独立试做，用试做结果检查题目难度和测试是否充分；正式比赛则按照相同的验收条件检查选手代码。

## 从题面到验收测试

我们先冻结产品题使用的 PowerContext 版本，让后续提交和重判都基于同一个产品版本。选手要在这个基础上完成题目要求的功能。例如，[CSV 采集题](https://github.com/oceanbase/powercontext/blob/2026-competition/competition/challenges/source-01-table-evidence/instruction.md)要求保留原始记录的身份，即使数据已经持久化、进程已经重启，引用仍能回查到原始记录。[混合检索题](https://github.com/oceanbase/powercontext/blob/2026-competition/competition/challenges/retrieval-02-hybrid-search/instruction.md)则要求减少数据库查询次数，同时保持过滤行为、引用身份和检索质量。这些行为和通过条件写在题面里，测试通过公开接口检查，具体算法和内部数据结构留给选手决定。

每道题按 [Harbor 的任务结构](https://docs.harborframework.com/tasks/overview)组织：`task.toml` 配置环境和时限，`instruction.md` 说明要求，测试入口负责调用验收逻辑。有性能要求的题目需要先通过必测的功能组，才能进入 benchmark，最终结果写入 Harbor 原生的 `reward.txt`。这样，赛前试做、正式判题和后续重判可以使用同一份任务定义。

选手本地使用的是从题库中拆出的公开自测部分，放在 [2026-competition 分支的 competition/ 目录](https://github.com/oceanbase/powercontext/tree/2026-competition/competition)。该分支保留冻结的产品基线，`competition/` 提供题面、公开样例、行为测试和固定的运行依赖。选手在产品的 `src/` 中实现功能后，就可以用 `make -C competition test-csv` 这类命令检查代码。测试直接从指定的产品目录加载源码，不依赖判题服务，也不需要模型 API Key 或平台 Token；如果本机不便配置依赖，还可以使用随包提供的离线 Docker 自测环境。正式判题还会用未公开的测试和数据检查其他输入、边界情况，以及题面声明的质量、成本和性能要求。

## 用 Agent 检查题目和测试

出题人熟悉参考解法，阅读题面时容易把自己知道、却没有写出来的条件也带进去。我们先用参考实现、起始代码（starter）和有意写错的实现检查测试，确认参考实现能够通过，starter 不能通过，针对性的错误也能被检出。随后再让 Agent 只根据公开材料独立解题，观察它在哪里遇到困难、怎样理解题目，以及是否采用了出题人没有考虑过的做法。

Harbor 已经[集成 Codex 等 Agent](https://docs.harborframework.com/agents/pre-integrated-agents)，所以我们直接使用它的原生 Codex Agent 试做产品题。每次尝试都从相同的干净基线开始，我们只提供公开题面和允许读取的材料，不提供参考解法，也不带入上一次尝试的修改。我们记录本次使用的模型、reasoning 设置、时限和容器资源；解题结束后，再由独立的 Verifier 验收留下的源码制品。测试组的通过情况、质量与成本指标和 Agent 执行轨迹一同保存，后续分析失败时可以相互对照。

[![同一份 Harbor 任务分别用于 Agent 独立试做和正式代码提交，两条路径各自进入独立 Verifier](../../../assets/blog/three-days-four-thousand-evaluations/trial-and-submission.png)](../../../assets/blog/three-days-four-thousand-evaluations/trial-and-submission.png "查看大图")

*图 1：试做时由 Codex Agent 编写实现，正式判题时由 SubmissionAgent 装载选手代码。两种情况下，都由独立的 Verifier 验收源码制品。*

我们为每题安排了三次独立试做。其中，财报 PDF 题的有些实现能够通过解析检查，却没有达到证据页检索的召回要求；混合检索题也出现过 SQL 数量和延迟已经达标，部分查询的检索质量仍然下降的情况。

自动处理题还暴露了测试本身的遗漏。题目要求新增来源达到数量门槛后触发后台工作，有些实现只在普通采集结束后唤醒处理。当最后一条记录通过另一条合法写入路径进入时，数量已经达标，后台工作却没有启动。题面已经包含这项要求，早期测试没有覆盖到这种写入顺序。我们补齐测试后，使用 Harbor 的[重判能力](https://docs.harborframework.com/jobs/regrade)重新验收保存的原始制品，无需 Agent 再次解题。

## 固定每次提交的代码版本

正式判题时，`SubmissionAgent` 只负责上传准备好的选手源码，验收和判分由 Verifier 完成。平台上的一次提交对应一个任务 ID，11 道题在这个任务内顺序执行，不同提交之间可以并发。服务按各题分值汇总比赛积分。

排队期间，选手可能继续更新分支。如果接入服务只保存分支名，最后判到的代码就可能与原先接受的提交不同，重启后也难以找回当时的版本。因此，我们保留平台给出的版本约束，在执行代码题之前解析实际分支和完整 commit，写入本次验收计划。准备 Hook 据此提取相对冻结产品基线的 `src/` 差异，检查修改路径是否在允许范围内，再把补丁应用到干净基线上。依赖和测试材料由组织方独立提供，评分条件随验收计划固定，选手仓库中的文件变化不会改变这些条件。

仓库准备题还需要检查官方 Fork 关联，或任一分支是否保留了冻结基线的历史。由于这些信息会随仓库变化，我们由可信的接入服务采集并保存 GitHub 元数据，交给离线验证器检查，复查时也使用这份已保存的证据。

[![比赛平台、版本冻结、本地调度、Harbor 单题执行与成绩回传之间的关系](../../../assets/blog/three-days-four-thousand-evaluations/submission-lifecycle.png)](../../../assets/blog/three-days-four-thousand-evaluations/submission-lifecycle.png "查看大图")

*图 2：接入服务先固定提交版本和验收计划，再由 Harbor 逐题执行，最后汇总成绩并回传平台。*

## 复用准备容器，隔离代码执行

同一份源码要接受多道题的验收。若每道题都启动一个提交容器，容器启动和源码传输的开销就会重复发生，在测试本身耗时较短时尤其明显。我们把这部分准备工作放进一个常驻提交容器，每次只租给一个 Harbor 任务，用于上传准备好的源码和收集制品，不在其中执行选手代码。制品收集完成后，任务立即归还租约，其他提交便可以开始准备；已经收集的源码则交给独立的 Verifier 容器执行。

容器复用后，上一次任务的残留就需要显式处理。每次取得租约，任务都会先清理旧源码和日志。如果文件传输被取消，或子进程异常退出留下未完成标记，我们会在下次租用前重启容器，终止可能还在运行的传输。执行代码的 Verifier 则各自拥有源码、数据库和临时目录，并按验收计划限制 CPU 和内存。平台 Token、仓库凭据和接入服务配置始终留在宿主侧。

[![常驻提交容器串行准备制品，多个独立 Verifier 并行执行](../../../assets/blog/three-days-four-thousand-evaluations/preparation-and-verifiers.png)](../../../assets/blog/three-days-four-thousand-evaluations/preparation-and-verifiers.png "查看大图")

*图 3：准备制品时串行租用常驻容器，完成后释放租约。各提交的 Verifier 可以在资源预算内并行执行。*

调度时，常驻容器的资源开销只计算一次，每个正在运行或恢复中的 Verifier 则分别占用并发名额、CPU 和内存预算。由于同一提交中的题目顺序执行，一份提交同时最多使用一个 Verifier。接入服务据此限制平台任务的领取数量和本地预取数量：资源不足时，任务继续留在平台队列中，以免领取后在本地长时间等待，白白消耗平台任务的租约。

性能测试的调度还要考虑其他任务的干扰。没有专用 CPU 集合时，性能 Job 会等其他 Job 退出后独占执行，这段时间不允许普通任务进入。如果为普通任务和性能任务划分了互不重叠的 CPU 集合，两类任务就可以同时运行，各性能 Job 再按配额租用互不重叠的 CPU。我们把隔离锁的持有范围限制在当前题目，题目结束就释放；若锁住整份提交，其中的普通功能题也会阻挡其他提交，延长不必要的等待。

## 把公共依赖准备好，再开始判题

容器之间可以共用的，是提前准备好的运行依赖。我们把固定依赖构建进共享镜像，启动服务前检查本地镜像、Harbor 运行时、Docker 和 Git，后续判题直接使用这份镜像，不再逐次构建和安装。数据库运行库也随镜像固定，但每次验收使用独立的临时数据库目录，通过真实 Runtime 检查写入、读回和重新打开后的状态。

网络按执行阶段配置：宿主负责获取仓库和采集 GitHub 证据，试做中的 Agent 需要访问模型。仓库凭据只留在宿主侧，模型认证通过原生 Agent 适配器临时注入。到了 PowerContext 的确定性验收阶段，Verifier 只读取准备好的源码和验收材料，全程离线运行。

[![公共运行依赖分别支持仓库接收、Agent 试做和离线验收，任务数据独立保存](../../../assets/blog/three-days-four-thousand-evaluations/shared-dependencies.png)](../../../assets/blog/three-days-four-thousand-evaluations/shared-dependencies.png "查看大图")

*图 4：虚线表示共用已准备的运行依赖，实线表示各阶段的访问或执行关系。离线验收不访问仓库和模型，每个任务使用独立数据。*

## 服务重启后，从哪里继续

服务重启时，一份提交可能已经完成了几道题，当前题目的容器也可能仍在运行。成绩回传超时带来的不确定性又不同：平台可能已经收到了成绩，只是接入服务没有收到确认。这些情况都不能简单地从头判题，否则既会重复占用执行资源，也可能让同一次提交产生不同的结果。

服务通过定时调度领取任务、协调执行和回传成绩，并将任务进度和结果保存在数据库中。并发检查和状态变更放在短事务里，耗时的 Git、HTTP 和 Harbor 操作在事务外执行，避免长时间占用事务。服务重启后会重新建立周期调度，再结合数据库记录和 Harbor 原生 Job 状态，判断哪些工作已经完成、哪些还需要继续。

恢复执行时，服务先核对已有 Job 和结果，完整结果直接复用。未完成的 Job 只有在确认旧容器已退出、验收材料与原计划一致且资源足够后，才能通过 Harbor 原生 resume 继续。接入进程退出不代表容器已经停止，核查期间仍保留任务的资源预算。

判题结束后，服务在同一个数据库事务中保存终态结果和待回传请求，并把任务标记为等待回传。请求进入 outbox 后，内容便固定下来，由单独的投递流程发送，直到收到平台确认（ACK）才记为已送达。因此，网络超时后的重试只需要重发已保存的请求，不必重新运行选手代码。

平台明确拒绝请求或重试次数达到上限时，服务停止自动投递并保留记录供核查。等待确认的结果积压过多时，接入服务暂停领取新任务。

[![任务运行、恢复、结果与 outbox 原子保存，以及成绩确认的状态流转](../../../assets/blog/three-days-four-thousand-evaluations/recovery-and-delivery.png)](../../../assets/blog/three-days-four-thousand-evaluations/recovery-and-delivery.png "查看大图")

*图 5：恢复执行时检查已有 Job 和容器；成绩回传超时时，重发 outbox 中保存的原请求。*

保留恢复所需的材料，也意味着要处理不断增长的存储占用。我们按任务 ID 归档相对冻结基线的源码补丁、摘要和源码审计结果；需要复查时，把补丁重新应用到共享基线上，再核对摘要，就能恢复当时的源码，不必重新访问选手仓库。完整的 worktree、Git 缓存和每题重复保存的源码，则要等成绩回传得到确认、满足留存时间，并确认执行结束后才清理。Harbor 原生结果、验收日志、冻结计划和回传记录继续保留，供后续复查使用。

## 总结

我们用 Harbor 执行每道题，把资源调度、故障恢复和成绩回传放在比赛接入层处理。固定产品基线、保存每次提交的确切版本，并保留当次运行的验收条件和结果，让赛前试做发现的问题能够用原始制品复查，也让正式判题在服务重启后仍能继续。

还有一些任务很难用固定断言评分，可以考虑让 Agent 在容器中运行待评对象、收集证据，再据此给出判断。这时需要先通过试判确定基本原则和评分基线，并保留原始输入与证据，供后续复评。
