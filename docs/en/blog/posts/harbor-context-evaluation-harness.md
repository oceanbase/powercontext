---
slug: harbor-context-evaluation-harness
title: Building end-to-end evaluation for PowerContext on Harbor
description: Starting with coding tasks, reuse Harbor's execution and verification mechanisms to extend context evaluation, cross-session tasks, agent integrations, and environment reuse.
cover: assets/blog/harbor-context-evaluation-harness/cover.png
cover_alt: Task execution, context services, and independent evaluations connected through clear interfaces in a reusable evaluation architecture.
date: 2026-11-05
updated: 2026-11-11
authors:
  - chojan-shang
categories:
  - Engineering
draft: false
pin: false
---

A developer settles an API contract in one conversation, then asks an agent to continue the implementation a few days later. A debugging session uncovers a useful lead, and the investigation continues in another session. Both depend on information accumulated earlier. For PowerContext, we want to know whether an agent can use that history to continue its work and deliver a valid result.

This goal calls for several kinds of evaluation. A retrieval query checks whether information can be found, a continuation conversation checks whether an agreement has been retained, and a complete coding task reveals whether that information improves the delivered result. Each has its own evaluation method, but environment preparation, agent invocation, and result collection share common requirements. Those requirements are where the E2E harness began.

## Finding an execution framework through coding tasks

Coding tasks reflect how developers actually use agents. They were also one of the scenarios we had in mind when we first built PowerContext. A context system can help an agent continue to follow established requirements after switching sessions.

From an evaluation perspective, coding tasks usually have concrete deliverables, and many acceptance criteria can be checked with executable tests. Examining how an agent completes a coding task also reveals where the context system participates, whether it retrieves useful information, and whether there are observable improvements in step count, token usage, and elapsed time.

While researching Terminal-Bench, we started looking at [Harbor](https://harborframework.com/). It already provides a way to organize tasks: the task description states the requirements, the environment supplies files and dependencies, the agent performs the work, and the Verifier checks the result. For tasks scored with executable tests, the verifier runs predefined checks and produces a score from the actual results. [Harbor's task structure](https://docs.harborframework.com/tasks/overview)

<figure class="fig" id="fig-coding-verification">
<div class="fig-canvas" style="--fw:960;--fh:384" role="img" aria-label="Code execution, predefined tests, and result verification in a coding task">
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
<div class="fig-panel" style="--x:16;--y:52;--w:210;--h:316"><header><b>Task definition</b></header></div>
<div class="fig-card center" style="--x:32;--y:100;--w:178;--h:60"><b>Instructions</b></div>
<div class="fig-card center" style="--x:32;--y:190;--w:178;--h:60"><b>Environment spec</b></div>
<div class="fig-card center" style="--x:32;--y:296;--w:178;--h:60"><b>Verification rules</b></div>
<div class="fig-panel outline accent" style="--x:256;--y:52;--w:356;--h:236"><header><b>Task environment</b></header></div>
<div class="fig-box accent" style="--x:278;--y:100;--w:150;--h:56"><b>Agent</b></div>
<div class="fig-box" style="--x:278;--y:200;--w:150;--h:64"><b>Workspace and tools</b></div>
<div class="fig-box" style="--x:462;--y:138;--w:132;--h:80"><b>Code and artifacts</b></div>
<div class="fig-box" style="--x:640;--y:136;--w:160;--h:84"><b>Verifier</b><span>Checks expected behavior</span></div>
<div class="fig-box solid" style="--x:824;--y:142;--w:136;--h:72"><b>Reward and test logs</b></div>
</div>
<figcaption>Figure 1: Submitted code is then run and checked. Predefined tests apply the same acceptance criteria across runs.</figcaption>
</figure>

A database WAL recovery task, for example, includes database files, a runtime environment, and verification scripts. We chose to reuse these native tasks and extend evaluation through Harbor's task model to avoid rebuilding that machinery. We integrate PowerContext into this workflow and collect evidence to compare agent behavior with and without it.

## Choosing where to extend Harbor

Task scores show whether the code meets the acceptance criteria. Capture and recall records help us analyze how PowerContext contributed to the task.

Reusing the native execution flow is simpler than implementing another framework. Tasks retain Harbor's environment and agent interfaces, Harbor Job executes them, and the Verifier checks their output as before. Task manifests declare the context behavior to observe, while the custom layer handles PowerContext integration and additional context evaluation.

<figure class="fig" id="fig-harbor-integration">
<div class="fig-canvas" style="--fw:960;--fh:548" role="img" aria-label="Harbor&#x27;s execution boundary, PowerContext integration, and two kinds of evaluation results">
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
<div class="fig-box" style="--x:0;--y:92;--w:170;--h:86"><b>Task manifest</b><span>Task selection and evaluation settings</span></div>
<div class="fig-panel dashed accent" style="--x:200;--y:30;--w:450;--h:300"><header><b>Harbor execution</b></header></div>
<div class="fig-box" style="--x:220;--y:104;--w:116;--h:56"><b>Harbor Job</b></div>
<div class="fig-box" style="--x:362;--y:104;--w:128;--h:56"><b>Host adapter</b></div>
<span class="fig-label muted small center" style="--x:426;--y:172">Native integration or ACP</span>
<div class="fig-box accent" style="--x:516;--y:104;--w:114;--h:56"><b>Agent</b></div>
<div class="fig-box" style="--x:500;--y:236;--w:130;--h:56"><b>Task verifier</b></div>
<div class="fig-box accent" style="--x:760;--y:84;--w:200;--h:96"><b>PowerContext Server</b><span>Captures and prepares context</span></div>
<span class="fig-label accent small center" style="--x:695;--y:108">Host integration</span>
<div class="fig-box" style="--x:465;--y:372;--w:200;--h:60"><b>Task outcome</b></div>
<div class="fig-box" style="--x:760;--y:372;--w:200;--h:60"><b>Context checks</b></div>
<span class="fig-label accent small end" style="--x:850;--y:250">Capture, provenance, recall</span>
<div class="fig-box solid" style="--x:465;--y:476;--w:495;--h:72"><b>Evidence and report</b><span>Records task outcome and context checks as separate dimensions</span></div>
</div>
<figcaption>Figure 2: Context integration connects through Harbor's interfaces. Native tasks keep their own environments and acceptance methods.</figcaption>
</figure>

Native scores and context evaluations are stored separately. For example, the Terminal-Bench Verifier determines whether the work is complete, while the additional evaluation checks whether inputs were captured, whether memory sources belong to this run's inputs, and whether queries returned the expected content. [Execution and evaluation contracts](https://github.com/oceanbase/powercontext/blob/20f64d72b13594ad834fcd342f5927d0d0102abb/e2e/bub/README.md)

Harbor offers another advantage: it already integrates common agents such as Codex, and other hosts that support ACP can connect through that protocol. Harbor manages execution throughout, while the host adapter adds the preparation required for context integration.

## Making history part of the task

If every requirement appears in the current prompt, it is difficult to observe whether the context system helps an agent continue earlier work. The task must express dependencies on information from previous sessions.

Harbor's multi-step tasks suit this scenario. Earlier steps provide information, later steps make related requests in new sessions, and the Verifier then checks the deliverable. One continuation sequence shares a PowerContext Scope, the space that stores context for that experiment. In these continuation tasks, the original conversation is not passed directly into later sessions.

For example, an initial session explicitly states that the search-result cache duration was 30 seconds and has now changed to 90 seconds. A later session asks for the current and previous values in a structured answer. The Verifier checks both fields, avoiding a pass for an answer that mentions both numbers but confuses their relationship.

<figure class="fig" id="fig-history-continuation">
<div class="fig-canvas" style="--fw:960;--fh:530" role="img" aria-label="An initial session, context preparation in the same Scope, a continuation session, and answer verification">
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
<div class="fig-panel" style="--x:0;--y:70;--w:330;--h:170"><header><b>Step 1: initial session</b></header></div>
<div class="fig-card" style="--x:16;--y:122;--w:190;--h:100"><b>User message</b><span>TTL was 30 s; now 90 s</span><span>Fix a README typo</span></div>
<div class="fig-box solid" style="--x:222;--y:138;--w:92;--h:68"><b>Agent session 1</b></div>
<div class="fig-panel" style="--x:360;--y:70;--w:600;--h:170"><header><b>Step 2: continuation session</b></header></div>
<div class="fig-card" style="--x:376;--y:122;--w:170;--h:100"><b>New prompt</b><span>Current TTL? Previous TTL?</span></div>
<div class="fig-box solid" style="--x:566;--y:138;--w:104;--h:68"><b>Agent session 2</b></div>
<div class="fig-card center" style="--x:690;--y:146;--w:124;--h:52"><b><code>answer.json</code></b></div>
<span class="fig-label muted small center" style="--x:392;--y:26">New session; the workspace is reset</span>
<div class="fig-box start" style="--x:600;--y:300;--w:240;--h:96"><b>Verifier</b><span><code>ttl_seconds = 90</code></span><span><code>previous_ttl_seconds = 30</code></span></div>
<div class="fig-box accent" style="--x:860;--y:321;--w:100;--h:54"><b>Task reward</b></div>
<div class="fig-panel accent" style="--x:0;--y:390;--w:560;--h:136"><header><b>PowerContext · same Scope</b></header></div>
<div class="fig-card center" style="--x:16;--y:442;--w:170;--h:60"><b>Captured Sources</b></div>
<div class="fig-card center" style="--x:206;--y:442;--w:140;--h:60"><b>Memory</b></div>
<div class="fig-card center" style="--x:366;--y:442;--w:178;--h:60"><b>Prepared context</b></div>
<span class="fig-label accent small" style="--x:110;--y:320">Capture</span>
<span class="fig-label accent small" style="--x:465;--y:340">Context preparation</span>
<span class="fig-label muted small wrap" style="--x:600;--y:430;--w:360">The continuation prompt does not include the original dialogue.</span>
</div>
<figcaption>Figure 3: The new session prepares context through the same Scope. The task's verifier then checks whether the answer distinguishes the current and previous values.</figcaption>
</figure>

We also adapted MemoryCode for ongoing coding collaboration. Its mentoring exchanges contain coding conventions, rule updates, and unrelated content. Each exchange becomes a separate session. The final session writes code following the earlier conventions, and MemoryCode's verification process checks the output.

Unlike the protocol in the original paper, our final session does not receive the original conversations. This tests whether an agent can use context from earlier sessions to follow the established conventions. After the preliminary stage, the task resets the workspace to reduce the opportunity to recover requirements from notes left there. [MemoryCode task design](https://github.com/oceanbase/powercontext/blob/20f64d72b13594ad834fcd342f5927d0d0102abb/e2e/bub/README.md#memorycode-workloads)

These tasks also support OFF/ON comparisons of agent behavior with and without PowerContext. The OFF group runs the native agent without PowerContext integration. The ON group enables the host's PowerContext integration, captures information from the initial sessions, and prepares relevant context for continuation. Both groups use the same task, host, and model configuration, and run in separate containers.

## Combining repeated preparation work

As the task collection grows, repeatedly starting environments and installing dependencies adds to execution costs. Even a short acceptance task can spend considerable time preparing its environment. Tasks with the same environment and controllable interference can share that preparation.

The Runner combines local acceptance tasks into one Harbor multi-step task when they are assigned to the same group and use compatible configurations. They run sequentially in a single container, with separate Scopes for context data but shared filesystem and process state. The task and host adapter clean residual session records, temporary files, and other state according to each task's retention rules. Tasks that need independent environments continue to run separately. [Task grouping in the Runner](https://github.com/oceanbase/powercontext/blob/20f64d72b13594ad834fcd342f5927d0d0102abb/e2e/bub/src/powercontext_e2e/runner.py)

<figure class="fig" id="fig-task-batching">
<div class="fig-canvas" style="--fw:960;--fh:470" role="img" aria-label="Compatible tasks running sequentially in a shared container with separately recorded evaluation results">
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
<div class="fig-box fill" style="--x:0;--y:40;--w:130;--h:60"><b>Task A manifest</b></div>
<div class="fig-box fill" style="--x:0;--y:136;--w:130;--h:60"><b>Task B manifest</b></div>
<div class="fig-card center" style="--x:170;--y:0;--w:170;--h:44"><b>Explicit batch group</b></div>
<div class="fig-box" style="--x:170;--y:80;--w:170;--h:76"><b>Compatibility check</b></div>
<span class="fig-label muted small center wrap" style="--x:255;--y:168;--w:200">Environment, execution and verification settings</span>
<div class="fig-panel outline accent" style="--x:380;--y:20;--w:580;--h:170"><header><b>One Harbor multi-step task · one container</b></header></div>
<div class="fig-box" style="--x:396;--y:74;--w:124;--h:50"><b>A: step 1</b></div>
<div class="fig-box" style="--x:536;--y:74;--w:124;--h:50"><b>A: step 2</b></div>
<div class="fig-box" style="--x:676;--y:74;--w:124;--h:50"><b>B: step 1</b></div>
<div class="fig-box" style="--x:816;--y:74;--w:124;--h:50"><b>B: step 2</b></div>
<span class="fig-label muted small center" style="--x:670;--y:146">Each step runs its own agent session</span>
<div class="fig-panel" style="--x:380;--y:250;--w:280;--h:132"><header><b>Task A records</b></header></div>
<div class="fig-card center" style="--x:396;--y:302;--w:116;--h:62"><b>Scope A</b></div>
<div class="fig-card center" style="--x:528;--y:302;--w:116;--h:62"><b>Evidence and grade</b></div>
<div class="fig-panel" style="--x:680;--y:250;--w:280;--h:132"><header><b>Task B records</b></header></div>
<div class="fig-card center" style="--x:696;--y:302;--w:116;--h:62"><b>Scope B</b></div>
<div class="fig-card center" style="--x:828;--y:302;--w:116;--h:62"><b>Evidence and grade</b></div>
<div class="fig-box fill start" style="--x:0;--y:410;--w:470;--h:56"><b>Shared filesystem and process state need task-level cleanup.</b></div>
<div class="fig-box fill start" style="--x:490;--y:410;--w:470;--h:56"><b>OFF / ON comparisons use separate containers.</b></div>
</div>
<figcaption>Figure 4: Compatible tasks share one environment setup and run in sequence. Each task keeps its own context, evidence, and scores.</figcaption>
</figure>

PowerContext's specialized evaluations of memory quality, performance, and other concerns still have their own execution flows. Scenarios suited to actual agent workflows can gradually become Harbor tasks; experiments that depend on specialized data processing and evaluation protocols remain with their respective suites.

## Conclusion

The harness uses Harbor's task execution and verification mechanisms, with PowerContext integration, cross-session tasks, and context evaluation. Task scores and context evaluations are stored separately, with runtime configuration and evidence retained for review.

Thanks to [Fengzdadi](https://github.com/Fengzdadi), [knqiufan](https://github.com/knqiufan), [Russell Lin](https://github.com/russell001209-ai), [ThunGuo](https://github.com/thunguo), and everyone who contributed to the design, implementation, and validation of the E2E harness.
