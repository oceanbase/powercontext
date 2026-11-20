---
slug: three-days-four-thousand-evaluations
title: Behind 4,000 evaluations in three days
description: How the database competition uses Harbor for task authoring, participant self-tests, and agent trial solving, with resource scheduling and recoverable judging.
cover: assets/blog/three-days-four-thousand-evaluations/cover.png
cover_alt: Submission records pass through a bounded queue and independent verification environments into persistent results and delivery records.
date: 2026-11-05
updated: 2026-11-18
authors:
  - chojan-shang
categories:
  - Engineering
draft: false
pin: false
---

During the first three days of the [sixth OceanBase Database Competition](https://open.oceanbase.com/competition), PowerContext's judging service handled more than 4,000 judging runs. The PowerContext problem set consists of 11 challenges worth 240 points, covering data ingestion, memory state, retrieval, and context construction.

We built this judging service specifically for the competition, using [Harbor](https://harborframework.com/) to manage execution environments, run tests, and save results. Before the competition, we had agents attempt the challenges independently to assess their difficulty and find gaps in the tests. During the competition, we check participant code against the same acceptance criteria.

## From requirements to acceptance tests

We first freeze the PowerContext version used by the product challenges, giving all submissions and later regrades the same product baseline. Participants work from this version to implement the required behavior. For example, the [CSV ingestion challenge](https://github.com/oceanbase/powercontext/blob/2026-competition/competition/challenges/source-01-table-evidence/instruction.md) requires record identity to survive persistence and a process restart, so citations can still resolve to the original records. The [hybrid search challenge](https://github.com/oceanbase/powercontext/blob/2026-competition/competition/challenges/retrieval-02-hybrid-search/instruction.md) asks participants to reduce database queries while preserving filtering behavior, citation identity, and retrieval quality. Each statement specifies the behavior and passing conditions, which the tests check through public interfaces. Participants choose the algorithms and internal data structures.

Each challenge follows [Harbor's task structure](https://docs.harborframework.com/tasks/overview): `task.toml` configures the environment and time limit, `instruction.md` describes the requirements, and a test entry point invokes the acceptance checks. Challenges with performance requirements must pass the mandatory functional groups before running benchmarks. The final result goes into Harbor's native `reward.txt`. This task definition is shared by trial attempts, competition judging, and regrades.

For local development, we extracted the public self-tests into the [competition/ directory on the 2026-competition branch](https://github.com/oceanbase/powercontext/tree/2026-competition/competition). The branch retains the frozen product baseline, while `competition/` supplies statements, public examples, behavior tests, and fixed runtime dependencies. Participants implement the functionality in the product's `src/`, then check their code with commands such as `make -C competition test-csv`. The tests load source directly from the selected product directory, so they need no connection to the judging service, model API keys, or platform tokens. Participants who cannot conveniently configure local dependencies can use the included offline Docker self-test environment. Formal judging uses held-out tests and data to check additional inputs, boundary cases, and the quality, cost, and performance requirements in the statement.

## Checking the tasks and tests with agent attempts

Authors know the reference solution and can unconsciously fill in conditions they have never put into the statement. We first check the tests against the reference implementation, starter code, and deliberately incorrect implementations, confirming that the reference passes, the starter fails, and the tests catch the targeted defects. We then have an agent solve the challenge independently using only the public materials. Its attempt shows where it struggles, how it interprets the requirements, and whether it takes an approach the author had not considered.

Harbor already [integrates agents such as Codex](https://docs.harborframework.com/agents/pre-integrated-agents), so we use its native Codex Agent for the product challenges. Every attempt starts from the same clean baseline. We supply only the public statement and permitted materials, with no reference solution, and carry no changes over from earlier attempts. We record the model, reasoning setting, time limit, and container resources for each run. After the agent finishes, an independent Verifier evaluates the source artifact it left behind. We save test-group outcomes, quality and cost measurements, and the agent trajectory together so we can compare them when investigating a failure.

<figure class="fig" id="fig-trial-and-submission">
<div class="fig-canvas" style="--fw:960;--fh:470" role="img" aria-label="One Harbor task supports independent agent solving and participant code submission, each with a separate verifier">
<svg viewBox="0 0 960 470">
<path class="fig-wire" d="M184 97 L200 97" marker-end="url(#fig-arrow)"/>
<path class="fig-wire" d="M374 97 L390 97" marker-end="url(#fig-arrow)"/>
<path class="fig-wire" d="M564 97 L580 97" marker-end="url(#fig-arrow)"/>
<path class="fig-wire" d="M754 97 L770 97" marker-end="url(#fig-arrow)"/>
<path class="fig-wire" d="M184 379 L200 379" marker-end="url(#fig-arrow)"/>
<path class="fig-wire" d="M374 379 L390 379" marker-end="url(#fig-arrow)"/>
<path class="fig-wire" d="M564 379 L580 379" marker-end="url(#fig-arrow)"/>
<path class="fig-wire" d="M754 379 L770 379" marker-end="url(#fig-arrow)"/>
<path class="fig-wire dashed" d="M670 190 L670 148" marker-end="url(#fig-arrow)"/>
<path class="fig-wire dashed" d="M670 250 L670 328" marker-end="url(#fig-arrow)"/>
</svg>
<div class="fig-panel" style="--x:0;--y:0;--w:960;--h:158"><header><b>Pre-competition calibration</b><span>Native Harbor task</span></header></div>
<div class="fig-box" style="--x:16;--y:52;--w:168;--h:90"><b>Public task + frozen starter</b></div>
<div class="fig-box" style="--x:206;--y:52;--w:168;--h:90"><b>Codex Agent</b><span>Implements the solution</span></div>
<div class="fig-box" style="--x:396;--y:52;--w:168;--h:90"><b>Code artifact</b></div>
<div class="fig-box accent" style="--x:586;--y:52;--w:168;--h:90"><b>Verifier</b></div>
<div class="fig-box" style="--x:776;--y:52;--w:168;--h:90"><b>Trial records</b><span>Outcomes, failures, time, quality</span></div>
<div class="fig-panel" style="--x:0;--y:282;--w:960;--h:158"><header><b>Competition judging</b><span>Native Harbor task</span></header></div>
<div class="fig-box" style="--x:16;--y:334;--w:168;--h:90"><b>Participant revision</b><span>Frozen baseline + src diff</span></div>
<div class="fig-box" style="--x:206;--y:334;--w:168;--h:90"><b>SubmissionAgent</b><span>Uploads prepared source</span></div>
<div class="fig-box" style="--x:396;--y:334;--w:168;--h:90"><b>Code artifact</b></div>
<div class="fig-box accent" style="--x:586;--y:334;--w:168;--h:90"><b>Verifier</b></div>
<div class="fig-box solid" style="--x:776;--y:334;--w:168;--h:90"><b>Competition result</b><span>Reward and task points</span></div>
<div class="fig-box fill" style="--x:586;--y:190;--w:168;--h:60"><b>Frozen evaluation assets</b></div>
<span class="fig-label muted small" style="--x:0;--y:450">Independent attempts and recorded evidence calibrate difficulty and test coverage.</span>
</div>
<figcaption>Figure 1: Codex Agent writes an implementation during calibration; SubmissionAgent loads participant code during formal judging. In both cases, an independent Verifier evaluates the source artifact.</figcaption>
</figure>

We ran three independent attempts for each challenge. Some implementations of the financial-report PDF task passed the parsing checks but fell short of the recall requirement for evidence-page retrieval. On hybrid search, we also saw implementations that met the SQL-count and latency limits while reducing retrieval quality for individual queries.

The automatic-processing task exposed a gap in the tests themselves. It requires background work to start when the number of new source entries reaches a threshold, but some implementations only woke processing after ordinary capture. When the final entry arrived through another valid write path, the count reached the threshold and the background work never started. The statement already covered this requirement; the early tests had missed that ordering of writes. After adding the checks, we used Harbor's [regrading support](https://docs.harborframework.com/jobs/regrade) to evaluate the saved original artifacts against the expanded tests without asking the agent to solve the task again.

## Freezing the code for each submission

During formal judging, `SubmissionAgent` only uploads the prepared participant source. A Verifier runs the acceptance checks and assigns the score. Each platform submission corresponds to one task ID, under which the 11 challenges run sequentially; different submissions can run concurrently. The service calculates the competition score using the point value of each challenge.

Participants can update their branches while a submission is queued. If the intake service saved only the branch name, the code eventually judged could differ from the accepted submission, and the service could struggle to recover the original version after a restart. We therefore retain the platform's revision constraints, resolve the actual branch and full commit before executing code challenges, and save them in the evaluation plan. A preparation hook uses this revision to extract the `src/` diff against the frozen product baseline, checks that the changed paths are permitted, and applies the patch to a clean baseline. The organizer supplies dependencies and test assets separately. Scoring conditions are fixed in the evaluation plan and cannot be changed by files in the participant's repository.

The repository-preparation task also checks the official fork relationship or whether any branch retains the frozen baseline's history. Because this information can change with the repository, trusted intake collects and saves the GitHub metadata for an offline verifier to inspect. Later reviews use that saved evidence as well.

<figure class="fig" id="fig-submission-lifecycle">
<div class="fig-canvas" style="--fw:960;--fh:486" role="img" aria-label="Competition intake, revision freezing, local scheduling, native Harbor jobs, and result delivery">
<svg viewBox="0 0 960 486">
<path class="fig-wire" d="M342 140 L352 140" marker-end="url(#fig-arrow)"/>
<path class="fig-wire" d="M468 140 L478 140" marker-end="url(#fig-arrow)"/>
<path class="fig-wire" d="M756 139 L765 139" marker-end="url(#fig-arrow)"/>
<path class="fig-wire" d="M850 139 L859 139" marker-end="url(#fig-arrow)"/>
<path class="fig-wire" d="M150 140 L210 140" marker-end="url(#fig-arrow)"/>
<path class="fig-wire" d="M608 140 L654 140" marker-end="url(#fig-arrow)"/>
<path class="fig-wire" d="M676 326 L614 326" marker-end="url(#fig-arrow)"/>
<path class="fig-wire" d="M430 326 L406 326" marker-end="url(#fig-arrow)"/>
<path class="fig-wire accent" d="M270 314 L156 314" marker-end="url(#fig-arrow-accent)"/>
<path class="fig-wire muted dashed" d="M150 342 L264 342" marker-end="url(#fig-arrow-muted)"/>
</svg>
<div class="fig-box" style="--x:0;--y:60;--w:150;--h:330"><b>Competition platform</b></div>
<div class="fig-panel dashed accent" style="--x:200;--y:20;--w:424;--h:400"><header><b>Intake service</b></header></div>
<div class="fig-box accent" style="--x:216;--y:100;--w:126;--h:80"><b>Intake</b><span>Freezes revision and plan</span></div>
<div class="fig-box" style="--x:358;--y:100;--w:110;--h:80"><b>SQLite task queue</b></div>
<div class="fig-box" style="--x:484;--y:100;--w:124;--h:80"><b>Resource admission</b><span>CPU · memory · concurrency</span></div>
<div class="fig-box" style="--x:270;--y:296;--w:130;--h:60"><b>Delivery</b></div>
<div class="fig-box" style="--x:430;--y:296;--w:178;--h:60"><b>Results + outbox</b></div>
<div class="fig-panel outline" style="--x:660;--y:60;--w:300;--h:330"><header><b>Native Harbor jobs</b></header></div>
<div class="fig-box" style="--x:676;--y:116;--w:80;--h:46"><b>Job 1</b></div>
<div class="fig-box" style="--x:770;--y:116;--w:80;--h:46"><b>Job 2</b></div>
<div class="fig-box" style="--x:864;--y:116;--w:80;--h:46"><b>…</b></div>
<span class="fig-label muted small center" style="--x:810;--y:178">Sequential within one submission</span>
<div class="fig-card center" style="--x:676;--y:296;--w:268;--h:60"><b>Verifier results and logs</b></div>
<span class="fig-label small center" style="--x:180;--y:116">Claim</span>
<span class="fig-label small center" style="--x:632;--y:116">Dispatch</span>
<span class="fig-label small center" style="--x:642;--y:302">Collect</span>
<span class="fig-label accent small center" style="--x:212;--y:290">Saved callback</span>
<span class="fig-label muted small center" style="--x:206;--y:352">Acknowledgment</span>
<div class="fig-box fill" style="--x:0;--y:432;--w:960;--h:54"><b>Task identity, full commit, evaluation assets and resource limits stay bound to the run.</b></div>
</div>
<figcaption>Figure 2: Intake fixes the submission revision and evaluation plan, Harbor executes the challenges, and intake aggregates and returns the scores.</figcaption>
</figure>

## Reusing preparation containers and isolating code execution

The same source code must be evaluated against several challenges. Starting a submission container for each one repeats the cost of container startup and source transfer, which is particularly noticeable when the tests themselves are short. We keep one resident submission container for this preparation work, leasing it exclusively to one Harbor task at a time. Inside it, the task uploads prepared source and collects artifacts without executing participant code. As soon as collection finishes, the task releases the lease so another submission can begin preparation, while a separate Verifier container executes the collected source.

Reusing a container means explicitly dealing with what the previous task left behind. Every task removes old source and logs when its lease begins. If a cancelled transfer or failed subprocess leaves an unfinished marker, we restart the container before the next lease to stop any transfer that may still be running. The Verifiers that execute code each have their own source, database, and temporary directories, with CPU and memory limits set by the evaluation plan. Platform tokens, repository credentials, and intake configuration remain on the host.

<figure class="fig" id="fig-preparation-and-verifiers">
<div class="fig-canvas" style="--fw:960;--fh:520" role="img" aria-label="A resident submission container prepares artifacts sequentially while independent verifiers run concurrently">
<svg viewBox="0 0 960 520">
<path class="fig-wire" d="M440 108 L534 108" marker-end="url(#fig-arrow)"/>
<path class="fig-wire" d="M652 108 L694 108" marker-end="url(#fig-arrow)"/>
<path class="fig-wire" d="M440 218 L534 218" marker-end="url(#fig-arrow)"/>
<path class="fig-wire" d="M652 218 L694 218" marker-end="url(#fig-arrow)"/>
<path class="fig-wire" d="M335 131 L335 189" marker-end="url(#fig-arrow)"/>
<path class="fig-wire" d="M440 328 L534 328" marker-end="url(#fig-arrow)"/>
<path class="fig-wire" d="M652 328 L694 328" marker-end="url(#fig-arrow)"/>
<path class="fig-wire" d="M335 241 L335 299" marker-end="url(#fig-arrow)"/>
<path class="fig-wire" d="M140 108 L164 108" marker-end="url(#fig-arrow)"/>
<path class="fig-wire muted dashed" d="M500 485 L670 485 Q680 485 680 475 L680 138 Q680 131 687 131 L694 131" marker-end="url(#fig-arrow-muted)"/>
<path class="fig-wire muted dashed" d="M500 485 L670 485 Q680 485 680 475 L680 248 Q680 241 687 241 L694 241" marker-end="url(#fig-arrow-muted)"/>
<path class="fig-wire muted dashed" d="M500 485 L670 485 Q680 485 680 475 L680 358 Q680 351 687 351 L694 351" marker-end="url(#fig-arrow-muted)"/>
</svg>
<div class="fig-box fill" style="--x:0;--y:85;--w:140;--h:46"><b>Frozen source</b></div>
<div class="fig-panel outline accent" style="--x:170;--y:20;--w:330;--h:416"><header><b>Resident submission container</b><span>One lease at a time</span></header></div>
<div class="fig-box accent" style="--x:230;--y:85;--w:210;--h:46"><b>Lease A</b></div>
<div class="fig-card center" style="--x:540;--y:85;--w:112;--h:46"><b>Artifact A</b></div>
<div class="fig-card" style="--x:700;--y:65;--w:260;--h:96"><b>Verifier A</b><span>Candidate code</span><span>Own database and temp files</span><span>CPU and memory limits</span></div>
<div class="fig-box" style="--x:230;--y:195;--w:210;--h:46"><b>Lease B</b></div>
<div class="fig-card center" style="--x:540;--y:195;--w:112;--h:46"><b>Artifact B</b></div>
<div class="fig-card" style="--x:700;--y:175;--w:260;--h:96"><b>Verifier B</b><span>Candidate code</span><span>Own database and temp files</span><span>CPU and memory limits</span></div>
<div class="fig-box" style="--x:230;--y:305;--w:210;--h:46"><b>Lease C</b></div>
<div class="fig-card center" style="--x:540;--y:305;--w:112;--h:46"><b>Artifact C</b></div>
<div class="fig-card" style="--x:700;--y:285;--w:260;--h:96"><b>Verifier C</b><span>Candidate code</span><span>Own database and temp files</span><span>CPU and memory limits</span></div>
<span class="fig-label muted small center" style="--x:335;--y:370">Upload source · collect artifact · release</span>
<span class="fig-label strong small center" style="--x:335;--y:396">Never runs participant code</span>
<span class="fig-label muted small center" style="--x:830;--y:30">Run independently, in parallel</span>
<div class="fig-box fill" style="--x:170;--y:460;--w:330;--h:50"><b>Frozen evaluation assets</b></div>
<span class="fig-label muted small center" style="--x:590;--y:492">Read-only input</span>
</div>
<figcaption>Figure 3: Artifact preparation leases the resident container serially and releases it when finished. Verifiers for different submissions can run concurrently within the resource budget.</figcaption>
</figure>

The scheduler accounts for the resident container once. Each running or recovering Verifier, however, needs its own concurrency slot, CPU, and memory reservation. Since challenges within a submission run sequentially, a submission uses at most one Verifier at a time. Intake uses this resource budget to limit both platform claims and local prefetch. When capacity is exhausted, tasks remain in the platform queue, avoiding a long local wait that would consume their platform leases.

Performance tests also need protection from other tasks. Without dedicated CPU sets, a performance Job waits for other Jobs to exit and then runs exclusively, with ordinary Jobs barred from starting during that interval. If ordinary and performance Jobs have disjoint CPU sets, both types can run at once; each performance Job then leases nonoverlapping CPUs according to its quota. We hold the isolation lock only for the current challenge and release it when that challenge finishes. Locking the whole submission would let its ordinary functional challenges block other submissions and prolong the wait unnecessarily.

## Preparing shared dependencies before judging

Containers can share the runtime dependencies prepared ahead of judging. We build the fixed dependencies into a shared image and check the local image, Harbor runtime, Docker, and Git before starting the service. Evaluations then use that image without repeating the build or dependency installation. Database libraries are fixed in the image as well, but each evaluation has its own temporary database directory. Tests use the native runtime to check writes, reads, and state after reopening.

Network access is configured by stage: the host fetches repositories and collects GitHub evidence, while agents access models during trial solving. Repository credentials stay on the host, and the native agent adapter supplies model authentication temporarily. During PowerContext's deterministic acceptance checks, the Verifier runs entirely offline, reading only the prepared source and evaluation assets.

<figure class="fig" id="fig-shared-dependencies">
<div class="fig-canvas" style="--fw:960;--fh:370" role="img" aria-label="Shared runtime preparation supports host intake, agent trials, and offline verification while task data stays independent">
<svg viewBox="0 0 960 370">
<path class="fig-wire accent dashed" d="M150 72 L150 122" marker-end="url(#fig-arrow-accent)"/>
<path class="fig-wire accent dashed" d="M480 72 L480 122" marker-end="url(#fig-arrow-accent)"/>
<path class="fig-wire accent dashed" d="M810 72 L810 122" marker-end="url(#fig-arrow-accent)"/>
<path class="fig-wire" d="M150 210 L150 284" marker-end="url(#fig-arrow)"/>
<path class="fig-wire" d="M480 210 L480 284" marker-end="url(#fig-arrow)"/>
<path class="fig-wire" d="M810 210 L810 238 Q810 248 800 248 L740 248 Q730 248 730 258 L730 280" marker-end="url(#fig-arrow)"/>
<path class="fig-wire" d="M810 248 L880 248 Q890 248 890 258 L890 280" marker-end="url(#fig-arrow)"/>
</svg>
<div class="fig-box accent" style="--x:0;--y:0;--w:960;--h:72"><b>Prepared runtime</b><span>Images · network · tools</span></div>
<div class="fig-box" style="--x:0;--y:128;--w:300;--h:82"><b>Host intake</b><span>Git + evidence</span></div>
<div class="fig-box" style="--x:330;--y:128;--w:300;--h:82"><b>Agent trials</b><span>Native agent</span></div>
<div class="fig-box" style="--x:660;--y:128;--w:300;--h:82"><b>Offline verification</b><span>Frozen inputs</span></div>
<div class="fig-box fill" style="--x:60;--y:290;--w:180;--h:60"><b>GitHub</b></div>
<div class="fig-box fill" style="--x:390;--y:290;--w:180;--h:60"><b>Model</b></div>
<div class="fig-box fill" style="--x:660;--y:286;--w:140;--h:72"><b>Task A</b><span>Own data</span></div>
<div class="fig-box fill" style="--x:820;--y:286;--w:140;--h:72"><b>Task B</b><span>Own data</span></div>
</div>
<figcaption>Figure 4: Dashed arrows indicate shared runtime preparation; solid arrows show access or execution within each stage. Offline verification does not access repositories or models, and each task owns its data.</figcaption>
</figure>

## Where to continue after a restart

When the service restarts, a submission may have completed several challenges while the current challenge still has a container running. A score-delivery timeout creates a different uncertainty: the platform may already have received the score, but intake has not received an acknowledgment. Restarting the evaluation from scratch in either case would repeat work, consume additional resources, and risk producing a different result for the same submission.

The service uses periodic scheduling to claim tasks, coordinate execution, and deliver scores, while a database stores task progress and results. Concurrency checks and state changes use short transactions; slow Git, HTTP, and Harbor operations run outside them to avoid holding transactions open. After a restart, the service recreates its periodic schedules and compares database records with native Harbor Job state to determine which work is complete and what remains to be done.

During execution recovery, the service checks existing Jobs and results and reuses complete results. An unfinished Job can continue through native Harbor resume only after the service confirms that old containers have stopped, evaluation assets match the original plan, and sufficient resources are available. The intake process exiting does not mean its containers have stopped, so resource reservations remain in place until these checks are complete.

Once judging finishes, a single database transaction saves the terminal result and callback request and marks the task as awaiting delivery. The request's content is fixed when it enters the outbox. A separate delivery process sends it and marks it delivered only after the platform acknowledges receipt (ACK). After a network timeout, the service can therefore resend the saved request without executing participant code again.

An explicit rejection or an exhausted retry limit stops automatic delivery, with the record retained for investigation. If too many results are waiting for acknowledgment, intake pauses new claims.

<figure class="fig" id="fig-recovery-and-delivery">
<div class="fig-canvas" style="--fw:960;--fh:520" role="img" aria-label="Execution, recovery, atomic result and outbox storage, and acknowledged result delivery">
<svg viewBox="0 0 960 520">
<path class="fig-wire" d="M140 148 L190 148" marker-end="url(#fig-arrow)"/>
<path class="fig-wire" d="M350 148 L434 148" marker-end="url(#fig-arrow)"/>
<path class="fig-wire accent" d="M610 148 L814 148" marker-end="url(#fig-arrow-accent)"/>
<path class="fig-wire muted" d="M790 148 L790 42 Q790 32 780 32 L472 32 Q462 32 462 42 L462 114" marker-end="url(#fig-arrow-muted)"/>
<path class="fig-wire" d="M273 176 L273 310" marker-end="url(#fig-arrow)"/>
<path class="fig-wire" d="M370 344 L395 344 Q405 344 405 334 L405 148"/>
<path class="fig-wire muted" d="M196 334 L178 334 Q170 334 170 342 L170 352 Q170 360 178 360 L190 360" marker-end="url(#fig-arrow-muted)"/>
<path class="fig-wire muted dashed" d="M283 372 L283 394"/>
</svg>
<div class="fig-panel dashed accent center" style="--x:410;--y:70;--w:350;--h:196"><header><b>One SQLite transaction</b></header></div>
<div class="fig-box" style="--x:0;--y:120;--w:140;--h:56"><b>queued</b></div>
<div class="fig-box" style="--x:196;--y:120;--w:154;--h:56"><b>running</b></div>
<div class="fig-box accent" style="--x:440;--y:120;--w:170;--h:56"><b>result_ready</b></div>
<div class="fig-card center" style="--x:440;--y:198;--w:290;--h:48"><b>Saved callback (outbox)</b></div>
<div class="fig-box solid" style="--x:820;--y:120;--w:140;--h:56"><b>delivered</b></div>
<span class="fig-label accent small center" style="--x:712;--y:156">Platform acknowledgment</span>
<span class="fig-label muted small center" style="--x:626;--y:8">Retry the identical saved payload</span>
<div class="fig-box" style="--x:196;--y:316;--w:174;--h:56"><b>recovering</b></div>
<span class="fig-label muted small end" style="--x:263;--y:222">Restart or uncertain ownership</span>
<span class="fig-label muted small" style="--x:380;--y:352">Reuse the completed result</span>
<span class="fig-label muted small end" style="--x:160;--y:336">Resume the native Job</span>
<div class="fig-card" style="--x:150;--y:394;--w:330;--h:116"><span>Check the native Job</span><span>Confirm old containers stopped</span><span>Match assets and resource budget</span><span>Resume with Harbor</span></div>
<div class="fig-box fill" style="--x:560;--y:446;--w:400;--h:64"><b>Uncertain execution keeps its resource reservations</b></div>
</div>
<figcaption>Figure 5: Execution recovery checks existing Jobs and containers. After a score-delivery timeout, the service resends the original request saved in the outbox.</figcaption>
</figure>

Keeping the material needed for recovery also means managing growing storage use. Under each task ID, we archive the source patch against the frozen baseline, its digests, and the source audit. Reapplying that patch to the shared baseline and checking the digests restores the source that was judged, without accessing the participant's repository again. We reclaim full worktrees, Git caches, and repeated per-challenge source only after score delivery is acknowledged, the retention interval has elapsed, and execution is confirmed to have stopped. Native Harbor results, verifier logs, frozen plans, and delivery records remain available for later review.

## Conclusion

Harbor executes each challenge, while our competition integration layer handles resource scheduling, recovery, and score delivery. By freezing the product baseline and saving each submission's exact revision, acceptance conditions, and results, we can revisit problems found during trial solving using the original artifacts. The same records also let formal judging continue after a service restart.

Some tasks are difficult to score with fixed assertions. For these, an agent could run the subject in a container, collect evidence, and use it to make a judgment. This would require pilot reviews to establish judging principles and a scoring baseline, along with preservation of the original inputs and evidence for later reevaluation.
