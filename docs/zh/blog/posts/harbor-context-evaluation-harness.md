---
slug: harbor-context-evaluation-harness
title: 基于 Harbor 构建 PowerContext 的端到端测评
description: 从编程任务出发，复用 Harbor 的执行与验证机制，逐步扩展上下文评价、跨会话任务、Agent 接入与运行环境复用。
cover: assets/blog/harbor-context-evaluation-harness/cover.png
cover_alt: 任务执行、上下文服务与独立评价通过清晰的接口连接，形成可复用的测评架构。
date: 2026-11-05
updated: 2026-11-11
authors:
  - chojan-shang
categories:
  - 工程实践
draft: false
pin: false
---

开发者在一次交流中确定了接口约定，几天后让 Agent 继续实现；排查故障时找到了一条线索，换个会话后还要接着调查。这些工作都依赖此前积累的信息。对于 PowerContext，我们关心的是：Agent 能否依据历史信息继续推进并交付有效的任务结果。

围绕这个目标，可以设计出不同层次的测评。一次检索能检查信息是否被找到，一段续接对话能检查约定是否被保留，完整的编程任务则能观察这些信息有没有对结果交付产生积极影响。这些测评各有评价方法，但环境准备、Agent 调用和结果收集有共同之处，E2E harness 的建设就从这部分共同需求开始。

## 从编程任务找到执行框架

编程任务接近开发人员实际使用 Agent 的方式，也是我们最开始构建 PowerContext 的一个预设场景。上下文系统可以帮助 Agent 在切换会话后继续遵循已经确定的要求。

而从测评的视角看，编程任务通常有明确的交付物，许多验收目标也能通过可执行测试验证。通过观察 Agent 完成编程任务的轨迹，也可以检查上下文系统是如何介入整个开发流程、是否召回了有效信息，以及任务步数、Token 开销和耗时上是否有可见收益。

在调研 Terminal-Bench 的过程中，我们开始关注 [Harbor](https://harborframework.com/)。它已经提供了一套组织任务的方式：任务描述说明工作要求，环境提供文件和依赖，Agent 执行操作，Verifier 检查结果。对于采用可执行测试评分的任务，验证器运行预先定义的检查，并根据实际结果产生评分。[Harbor 的任务结构](https://docs.harborframework.com/tasks/overview)

<figure class="fig" id="fig-coding-verification">
<div class="fig-canvas" style="--fw:960;--fh:384" role="img" aria-label="编程任务中的代码执行、预设测试与结果验证">
<svg viewBox="0 0 960 384">
<path class="fig-wire" d="M210 130 L272 130" marker-end="url(#fig-arrow)"/>
<path class="fig-wire" d="M210 220 L250 220" marker-end="url(#fig-arrow)"/>
<path class="fig-wire" d="M353 162 L353 194" marker-end="url(#fig-arrow)" marker-start="url(#fig-arrow)"/>
<path class="fig-wire" d="M428 128 L437 128 Q446 128 446 137 L446 173 Q446 178 451 178 L456 178" marker-end="url(#fig-arrow)"/>
<path class="fig-wire" d="M428 232 L437 232 Q446 232 446 223 L446 178"/>
<path class="fig-wire" d="M594 178 L634 178" marker-end="url(#fig-arrow)"/>
<path class="fig-wire accent" d="M800 178 L818 178" marker-end="url(#fig-arrow-accent)"/>
<path class="fig-wire" d="M210 326 L710 326 Q720 326 720 316 L720 226" marker-end="url(#fig-arrow)"/>
</svg>
<div class="fig-panel frame" style="--x:0;--y:0;--w:960;--h:384"><header><b>Harbor trial</b></header></div>
<div class="fig-panel" style="--x:16;--y:52;--w:210;--h:316"><header><b>任务定义</b></header></div>
<div class="fig-card center" style="--x:32;--y:100;--w:178;--h:60"><b>任务说明</b></div>
<div class="fig-card center" style="--x:32;--y:190;--w:178;--h:60"><b>环境定义</b></div>
<div class="fig-card center" style="--x:32;--y:296;--w:178;--h:60"><b>验证规则</b></div>
<div class="fig-panel outline accent" style="--x:256;--y:52;--w:356;--h:236"><header><b>任务环境</b></header></div>
<div class="fig-box accent" style="--x:278;--y:100;--w:150;--h:56"><b>Agent</b></div>
<div class="fig-box" style="--x:278;--y:200;--w:150;--h:64"><b>工作区与工具</b></div>
<div class="fig-box" style="--x:462;--y:138;--w:132;--h:80"><b>代码与产物</b></div>
<div class="fig-box" style="--x:640;--y:136;--w:160;--h:84"><b>验证器</b><span>按预期行为检查</span></div>
<div class="fig-box solid" style="--x:824;--y:142;--w:136;--h:72"><b>奖励与测试日志</b></div>
</div>
<figcaption>图 1：代码提交后，还要经过运行和检查。预设测试让不同运行采用相同的验收标准。</figcaption>
</figure>

以数据库 WAL 恢复任务为例，其中包含数据库文件、运行环境和验证脚本。为了避免重复建设，我们决定复用这些原生任务，并沿着 Harbor 的任务模型扩展测评。在此基础上，我们把 PowerContext 接入执行流程，并收集运行证据，比较接入与未接入时 Agent 的表现。

## 定制放在 Harbor 的哪些接口上

任务评分反映代码是否通过验收，采集和召回记录则帮助分析 PowerContext 在任务中的作用。

相比实现另一套执行框架，直接复用原生的执行流程是更简单的方式：任务仍采用 Harbor 的环境和 Agent 接口，由 Harbor Job 执行，Verifier 按原来的方式检查产出。任务清单声明要观察的上下文行为，定制层负责 PowerContext 接入和额外的上下文评价。

<figure class="fig" id="fig-harbor-integration">
<div class="fig-canvas" style="--fw:960;--fh:548" role="img" aria-label="Harbor 执行边界、PowerContext 集成与两类评价结果">
<svg viewBox="0 0 960 548">
<path class="fig-wire" d="M170 135 L214 132" marker-end="url(#fig-arrow)"/>
<path class="fig-wire" d="M336 132 L356 132" marker-end="url(#fig-arrow)"/>
<path class="fig-wire" d="M490 132 L510 132" marker-end="url(#fig-arrow)"/>
<path class="fig-wire accent" d="M636 132 L754 132" marker-end="url(#fig-arrow-accent)" marker-start="url(#fig-arrow-accent)"/>
<path class="fig-wire" d="M573 160 L573 230" marker-end="url(#fig-arrow)"/>
<path class="fig-wire" d="M565 292 L565 366" marker-end="url(#fig-arrow)"/>
<path class="fig-wire accent dashed" d="M860 180 L860 366" marker-end="url(#fig-arrow-accent)"/>
<path class="fig-wire" d="M565 432 L565 442 Q565 452 575 452 L703 452 Q712 452 712 461 L712 470" marker-end="url(#fig-arrow)"/>
<path class="fig-wire" d="M860 432 L860 442 Q860 452 850 452 L712 452"/>
</svg>
<div class="fig-box" style="--x:0;--y:92;--w:170;--h:86"><b>任务清单</b><span>任务选择与评测设置</span></div>
<div class="fig-panel dashed accent" style="--x:200;--y:30;--w:450;--h:300"><header><b>Harbor 执行</b></header></div>
<div class="fig-box" style="--x:220;--y:104;--w:116;--h:56"><b>Harbor Job</b></div>
<div class="fig-box" style="--x:362;--y:104;--w:128;--h:56"><b>宿主适配器</b></div>
<span class="fig-label muted small center" style="--x:426;--y:172">原生集成或 ACP</span>
<div class="fig-box accent" style="--x:516;--y:104;--w:114;--h:56"><b>Agent</b></div>
<div class="fig-box" style="--x:500;--y:236;--w:130;--h:56"><b>任务验证器</b></div>
<div class="fig-box accent" style="--x:760;--y:84;--w:200;--h:96"><b>PowerContext Server</b><span>采集并准备上下文</span></div>
<span class="fig-label accent small center" style="--x:695;--y:108">宿主集成</span>
<div class="fig-box" style="--x:465;--y:372;--w:200;--h:60"><b>任务结果</b></div>
<div class="fig-box" style="--x:760;--y:372;--w:200;--h:60"><b>上下文检查</b></div>
<span class="fig-label accent small end" style="--x:850;--y:250">采集、来源与召回</span>
<div class="fig-box solid" style="--x:465;--y:476;--w:495;--h:72"><b>证据与报告</b><span>分别记录任务结果与上下文检查两个维度</span></div>
</div>
<figcaption>图 2：上下文集成沿着 Harbor 的接口接入，原生任务继续使用自己的环境和验收方式。</figcaption>
</figure>

原生评分和上下文评价分别保存。例如，Terminal-Bench 的 Verifier 判断工作是否完成，附加评价检查输入是否被采集、记忆来源是否属于本次输入，以及查询是否返回了预期内容。[执行与评价约定](https://github.com/oceanbase/powercontext/blob/20f64d72b13594ad834fcd342f5927d0d0102abb/e2e/bub/README.md)

使用 Harbor 还有另外一个好处，它已经提供了对 Codex 等常见 Agent 的集成，支持 ACP 的其他宿主也可以通过该协议接入。整个执行过程由 Harbor 统一管理，宿主适配层补充上下文集成所需的准备工作。

## 让历史信息成为任务的一部分

如果把全部要求都写在当前提示中，就很难观察上下文系统能否帮助 Agent 延续此前的工作。为此，任务还要表达信息在不同会话之间的依赖。

Harbor 的多步骤任务适合承载这类场景。前面的步骤提供信息，后面的步骤在新会话中提出相关要求，最后检查交付物。同一条续接链路共用 PowerContext 中的一个 Scope，也就是存放这次实验上下文的空间。在这些续接任务里，原始对话不会直接带入后续会话。

例如，一个任务在前置会话中明确说明：搜索结果缓存时间原为 30 秒，现已调整为 90 秒。后续会话询问当前值和旧值，要求写出结构化答案。Verifier 分别检查两个字段，避免把提到了两个数字、却混淆新旧关系的回答判为正确。

<figure class="fig" id="fig-history-continuation">
<div class="fig-canvas" style="--fw:960;--fh:530" role="img" aria-label="前置会话、同一 Scope 的上下文准备、续接会话与答案检查">
<svg viewBox="0 0 960 530">
<path class="fig-wire" d="M206 172 L216 172" marker-end="url(#fig-arrow)"/>
<path class="fig-wire" d="M546 172 L560 172" marker-end="url(#fig-arrow)"/>
<path class="fig-wire" d="M670 172 L684 172" marker-end="url(#fig-arrow)"/>
<path class="fig-wire muted dashed" d="M165 70 L165 56 Q165 46 175 46 L609 46 Q618 46 618 55 L618 64" marker-end="url(#fig-arrow-muted)"/>
<path class="fig-wire" d="M752 198 L752 294" marker-end="url(#fig-arrow)"/>
<path class="fig-wire" d="M840 348 L854 348" marker-end="url(#fig-arrow)"/>
<path class="fig-wire" d="M186 472 L200 472" marker-end="url(#fig-arrow)"/>
<path class="fig-wire" d="M346 472 L360 472" marker-end="url(#fig-arrow)"/>
<path class="fig-wire accent" d="M100 222 L100 436" marker-end="url(#fig-arrow-accent)"/>
<path class="fig-wire accent" d="M455 442 L455 336 Q455 326 465 326 L608 326 Q618 326 618 316 L618 212" marker-end="url(#fig-arrow-accent)"/>
</svg>
<div class="fig-panel" style="--x:0;--y:70;--w:330;--h:170"><header><b>第 1 步：前置会话</b></header></div>
<div class="fig-card" style="--x:16;--y:122;--w:190;--h:100"><b>用户消息</b><span>TTL 原为 30 秒，现为 90 秒</span><span>修复 README 笔误</span></div>
<div class="fig-box solid" style="--x:222;--y:138;--w:92;--h:68"><b>Agent 会话 1</b></div>
<div class="fig-panel" style="--x:360;--y:70;--w:600;--h:170"><header><b>第 2 步：续接会话</b></header></div>
<div class="fig-card" style="--x:376;--y:122;--w:170;--h:100"><b>新的提问</b><span>当前 TTL？之前的 TTL？</span></div>
<div class="fig-box solid" style="--x:566;--y:138;--w:104;--h:68"><b>Agent 会话 2</b></div>
<div class="fig-card center" style="--x:690;--y:146;--w:124;--h:52"><b><code>answer.json</code></b></div>
<span class="fig-label muted small center" style="--x:392;--y:26">新会话；工作区重置</span>
<div class="fig-box start" style="--x:600;--y:300;--w:240;--h:96"><b>验证器</b><span><code>ttl_seconds = 90</code></span><span><code>previous_ttl_seconds = 30</code></span></div>
<div class="fig-box accent" style="--x:860;--y:321;--w:100;--h:54"><b>任务奖励</b></div>
<div class="fig-panel accent" style="--x:0;--y:390;--w:560;--h:136"><header><b>PowerContext · 同一 Scope</b></header></div>
<div class="fig-card center" style="--x:16;--y:442;--w:170;--h:60"><b>已采集的 Source</b></div>
<div class="fig-card center" style="--x:206;--y:442;--w:140;--h:60"><b>Memory</b></div>
<div class="fig-card center" style="--x:366;--y:442;--w:178;--h:60"><b>准备好的上下文</b></div>
<span class="fig-label accent small" style="--x:110;--y:320">采集</span>
<span class="fig-label accent small" style="--x:465;--y:340">上下文准备</span>
<span class="fig-label muted small wrap" style="--x:600;--y:430;--w:360">续接提问中不包含原始对话。</span>
</div>
<figcaption>图 3：新会话通过同一 Scope 准备上下文，最终由任务的验证器检查答案是否分清了新旧值。</figcaption>
</figure>

我们也适配了 MemoryCode，用来测试持续编程协作。数据中的指导交流包含编码约定、规则更新和无关内容，每次交流对应一个独立会话。最终会话按此前约定编写代码，再沿用 MemoryCode 的验证方法检查产出。

与原始论文的约定不同，我们的最终会话不再提供原始对话，考察 Agent 能否借助跨会话上下文遵循约定。前置阶段结束后，任务会重置工作区，减少 Agent 通过工作区笔记恢复要求的机会。[MemoryCode 任务设计](https://github.com/oceanbase/powercontext/blob/20f64d72b13594ad834fcd342f5927d0d0102abb/e2e/bub/README.md#memorycode-workloads)

这些任务还能用于 OFF／ON 对照，比较接入 PowerContext 前后 Agent 的表现。OFF 组运行未接入 PowerContext 的原生 Agent；ON 组启用对应宿主的 PowerContext 集成，采集前置会话的信息，并在续接时准备相关上下文。两组使用相同的任务、宿主和模型配置，分别在独立容器中运行。

## 把重复的准备工作合在一起

任务集合扩展后，重复启动环境和安装依赖会增加运行成本。一个很短的验收任务，也可能花费不少时间准备环境。对于环境一致、状态干扰能够控制的任务，可以合并这部分准备工作。

Runner 把预先声明为同组、配置兼容的本地验收任务组合成一个 Harbor 多步骤任务，在同一容器中顺序执行。各任务使用独立 Scope 区分上下文数据，文件系统和进程状态仍然共享。任务与宿主适配层按各任务的状态保留规则清理会话记录、临时文件等残留；需要独立环境的任务继续单独运行。[Runner 的任务分组实现](https://github.com/oceanbase/powercontext/blob/20f64d72b13594ad834fcd342f5927d0d0102abb/e2e/bub/src/powercontext_e2e/runner.py)

<figure class="fig" id="fig-task-batching">
<div class="fig-canvas" style="--fw:960;--fh:470" role="img" aria-label="兼容任务的顺序执行、共享容器与分别记录的评价结果">
<svg viewBox="0 0 960 470">
<path class="fig-wire" d="M130 70 L139 70 Q148 70 148 79 L148 110 Q148 118 156 118 L164 118" marker-end="url(#fig-arrow)"/>
<path class="fig-wire" d="M130 166 L139 166 Q148 166 148 157 L148 118"/>
<path class="fig-wire" d="M255 44 L255 74" marker-end="url(#fig-arrow)"/>
<path class="fig-wire" d="M520 99 L530 99" marker-end="url(#fig-arrow)"/>
<path class="fig-wire" d="M660 99 L670 99" marker-end="url(#fig-arrow)"/>
<path class="fig-wire" d="M800 99 L810 99" marker-end="url(#fig-arrow)"/>
<path class="fig-wire accent" d="M340 118 L374 118" marker-end="url(#fig-arrow-accent)"/>
<path class="fig-wire dashed" d="M458 124 L458 244" marker-end="url(#fig-arrow)"/>
<path class="fig-wire dashed" d="M880 124 L880 244" marker-end="url(#fig-arrow)"/>
</svg>
<div class="fig-box fill" style="--x:0;--y:40;--w:130;--h:60"><b>任务 A 清单</b></div>
<div class="fig-box fill" style="--x:0;--y:136;--w:130;--h:60"><b>任务 B 清单</b></div>
<div class="fig-card center" style="--x:170;--y:0;--w:170;--h:44"><b>显式批次分组</b></div>
<div class="fig-box" style="--x:170;--y:80;--w:170;--h:76"><b>兼容性检查</b></div>
<span class="fig-label muted small center wrap" style="--x:255;--y:168;--w:200">环境、执行与验证设置</span>
<div class="fig-panel outline accent" style="--x:380;--y:20;--w:580;--h:170"><header><b>一个 Harbor 多步任务 · 一个容器</b></header></div>
<div class="fig-box" style="--x:396;--y:74;--w:124;--h:50"><b>A：第 1 步</b></div>
<div class="fig-box" style="--x:536;--y:74;--w:124;--h:50"><b>A：第 2 步</b></div>
<div class="fig-box" style="--x:676;--y:74;--w:124;--h:50"><b>B：第 1 步</b></div>
<div class="fig-box" style="--x:816;--y:74;--w:124;--h:50"><b>B：第 2 步</b></div>
<span class="fig-label muted small center" style="--x:670;--y:146">每一步使用独立的 Agent 会话</span>
<div class="fig-panel" style="--x:380;--y:250;--w:280;--h:132"><header><b>任务 A 记录</b></header></div>
<div class="fig-card center" style="--x:396;--y:302;--w:116;--h:62"><b>Scope A</b></div>
<div class="fig-card center" style="--x:528;--y:302;--w:116;--h:62"><b>证据与评分</b></div>
<div class="fig-panel" style="--x:680;--y:250;--w:280;--h:132"><header><b>任务 B 记录</b></header></div>
<div class="fig-card center" style="--x:696;--y:302;--w:116;--h:62"><b>Scope B</b></div>
<div class="fig-card center" style="--x:828;--y:302;--w:116;--h:62"><b>证据与评分</b></div>
<div class="fig-box fill start" style="--x:0;--y:410;--w:470;--h:56"><b>共享的文件系统和进程状态需要按任务清理。</b></div>
<div class="fig-box fill start" style="--x:490;--y:410;--w:470;--h:56"><b>OFF / ON 对照使用各自独立的容器。</b></div>
</div>
<figcaption>图 4：兼容任务共用一次环境准备，按顺序执行，每个任务分别保存上下文、证据和评分。</figcaption>
</figure>

PowerContext 的记忆质量、性能等专项测评仍有各自的执行流程。适合实际 Agent 工作过程的场景，可以逐步组织为 Harbor 任务；依赖专门数据处理和评价协议的实验，继续由相应套件负责。

## 总结

这套 harness 沿用 Harbor 的任务执行与验证机制，补充了 PowerContext 接入、跨会话任务和上下文评价。任务评分和上下文评价分别保存，并保留运行配置和证据供复查。

感谢 [Fengzdadi](https://github.com/Fengzdadi)、[knqiufan](https://github.com/knqiufan)、[Russell Lin](https://github.com/russell001209-ai)、[ThunGuo](https://github.com/thunguo)，以及参与 E2E harness 设计、实现和验证的贡献者。
