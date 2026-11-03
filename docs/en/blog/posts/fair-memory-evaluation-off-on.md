---
slug: fair-memory-evaluation-off-on
title: Before scoring a memory system, hide the answers
description: While building a cross-host OFF/ON comparison, I found three ways answers leaked into the runs. Beyond the scores, the comparison also caught a recall path that degraded without a trace.
cover: assets/blog/fair-memory-evaluation-off-on/cover.png
cover_alt: Two identical sealed paper rooms stand side by side; only a blue memory ribbon from a box labeled PowerContext passes through a single port to deliver a card to the desk in the right room.
date: 2026-11-02
authors:
  - fengzc
categories:
  - Engineering
draft: false
pin: false
---

Does a memory plugin actually make an agent better? The direct way to find out is a controlled experiment: give the same task to one agent with PowerContext off (OFF) and another with it on (ON), and see which one answers correctly.

That sounds simple. Once I actually built the evaluation, I found that the hard part is not getting ON to answer correctly. It is **confirming that ON answered correctly because memory worked, and not because the answer leaked from somewhere else**. A memory system exists to show an agent past information, so when you evaluate one, every unplanned information channel contaminates the final score.

This post covers the three answer leaks I tracked down while building the cross-host OFF/ON comparison in the PowerContext repository ([`e2e/bub`](https://github.com/oceanbase/powercontext/tree/master/e2e/bub)), and a hidden performance degradation the comparison caught outside the scores.

## How the comparison works

The tasks are cross-session continuations:

- **First session (Capture)**: while handing the agent an unrelated chore, such as fixing a typo in `README.md`, the user mentions a project decision or fact in passing—for example, which database was chosen and how many shards it uses.
- **Last session (Recall)**: in a brand-new session, the agent is asked to recall the earlier decision and write it to a JSON file as named fields. The grader checks the exact field values instead of matching keywords, so a hedged answer such as "either A or B" gets no credit.

One task also sets a deliberate trap with an updated value: a single message says the search result cache TTL used to be 30 seconds and was later raised to 90 seconds. An agent that keeps only the original value scores 0.

Each task runs once in an OFF container and once in a separate ON container, and when a task runs for several trials, the arm that goes first alternates. Both arms use the same image, host version, model, reasoning settings, and budget. The directory still carries the name of the first host it supported, `e2e/bub`, but the comparison now covers six hosts: **Bub, Codex, Claude Code, OpenCode, Pi, and Kimi Code**. The ON arm uses the default behavior each host's plugin gives its real users.

On paper, the design already looked fair. Once it actually ran, the problems came one after another.

## Where the answers leak in

### 1. The OFF arm goes looking for memory

During a pilot on OpenCode with GLM 5.3, neither OFF agent simply answered "I don't know" in the Recall session. Both started searching the container:

- They listed `/opt`, followed it to the PowerContext sources mounted there for plugin installation, read the plugin's Skill and client code, and pulled out the Server URL and API paths.
- They sent six requests to the Server with `curl` and Node's `fetch`. Every one was refused, but only because they tried the default address from the plugin code and did not guess the address that actually worked on the container network.
- One opened OpenCode's own local session database with `sqlite3`, looking for the earlier session's history.

Both OFF runs still scored 0, but only by luck. The pilot Server had no authentication yet, and it held the memory that earlier trials' ON arms had left behind: the answers. Worse, the search pushed the OFF Recall session's input tokens from about 30k in normal runs to 400k–540k, which distorted the OFF/ON cost comparison as well.

Continuation tasks ask exactly "what did we decide earlier?", which triggers an agent's urge to hunt for clues much more than a plain coding task does.

So [#1816](https://github.com/oceanbase/powercontext/pull/1816) redrew the boundary of the OFF arm: **it must be equivalent to a real user who has never used PowerContext**.

- The OFF container installs no plugin, mounts no PowerContext sources or files, and receives no related environment variables. The agent is never told that its memory has been turned off.
- The Server must enforce authentication (`enforced`), and only the ON arm receives an access token. Before the first run, the harness checks the Server and refuses to run if it lists Scopes without a token.

### 2. The host keeps its own records on disk

Even with every trace of PowerContext removed, the agent host itself leaves traces of earlier sessions on disk. The underlying evaluation framework, Harbor, reuses one container across the steps of a multi-step task, so data one step writes to disk is easy for the next session to read:

- **Bub** writes each session's tape to a file.
- **OpenCode**'s data directory keeps full session records, oversized tool output that was truncated, plans, and snapshots.
- **Pi**'s source shows that its `bash` tool writes the full output of commands that exceed its length limit to the temporary directory and never cleans it up.

The evaluation itself can leak the answer too. The Recall session's grading script, mounted in the container's `/tests` directory, contains the expected answer in plain text. And during the Capture session, a diligent agent may write the project fact into the workspace's `README.md` as a note.

So before every session, in both the OFF and ON arms, the harness cleans up: it removes the host's known local session files (keeping only login credentials), empties `/tests`, and resets the workspace after the Capture session. For OpenCode it also checks that the session list is really empty afterwards, and stops the evaluation if it is not.

This narrows the channel; it does not seal it. Every new host means going through its source and data directories first to learn where it keeps its state, and several of OpenCode's leftover directories were caught only during code review. The reset also covers the workspace alone: if the agent writes a note somewhere else in the container during Capture, the Recall session can still read it. This residual channel is open to the OFF and ON arms alike, so it is more likely to lift both arms' scores and narrow the gap between them than to favor ON.

### 3. The ON arm can read other trials' answers

Authentication keeps the OFF arm out, but not the ON arm. The default Server supports a single static administrator token, so the ON arm holds full privileges. When a task runs for several trials, the Scopes created by earlier trials already hold this task's answer. (A Scope is PowerContext's basic unit for isolating one project's context.) The host plugin's own tools only access the current trial's Scope, but an overly clever agent can take the token from its environment variables and write its own requests against other Scopes.

The default Server cannot issue a restricted, Scope-level token for each trial, so [#2035](https://github.com/oceanbase/powercontext/pull/2035) takes a different approach: **if you cannot block it beforehand, audit it afterwards**.

A Server with authentication enforced records every authorization decision in its Access Audit. After each ON arm, the harness pulls the audit records for that time window. If the Server allowed this ON arm to read another Scope's data, the run is marked as contaminated and left out of the success rate.

One statistical detail is easy to get wrong: **once a cross-Scope read happens, the run must be excluded unconditionally, whether or not it answered correctly**. Excluding only the runs that peeked and passed would keep the ones that peeked and failed in the sample, pulling the success rate down artificially and introducing selection bias in the other direction.

The audit has two preconditions. The audit records cannot tell whether a request came from the agent in the container or from the harness on the host, so the Server must be dedicated to the current evaluation while it runs, and the Server's clock must agree with the harness's. In a small pilot on Kimi Code (2 trials), the audit confirmed that no ON run accessed another Scope.

## Beyond the scores: catching a silent recall degradation

The moment an evaluation framework pays for itself is often not when you look at the total score.

In mid-October, while analyzing the evaluation pipeline on Pi and OpenCode, the ON arm's pass rate was a perfect 100%. But the Server-side statistics showed something odd: in 3 of 4 ON Recall sessions, the context the plugin prepared through its Hook (`POST /v1/context/prepare`) before the turn was empty (`ready_preparations: 0`). The agents still answered correctly only because the model, finding nothing in its context, called the `pc_search` tool itself and pulled the memory back.

Following that thread ([#2039](https://github.com/oceanbase/powercontext/issues/2039)) showed that, to avoid holding up the start of a host turn, the Server hard-coded a **0.25 s** timeout on query embedding while preparing context. On timeout, retrieval silently fell back to full-text search (FTS), with no log and no error in the response.

In the actual tests, a remote embedding call through OpenRouter (such as `text-embedding-3-small`) took **0.27–0.41 s** per round trip, just past the 0.25 s limit. After the fallback, the query the Hook sent was a long prompt carrying the full task instructions, which could not pass full-text search's admission floor of matching at least 25% of the terms. Memory that was sitting right there in storage could not be recalled.

The 100% pass rate hid the fault completely, but the cost landed squarely on the bill and the latency ([#2040](https://github.com/oceanbase/powercontext/issues/2040)). Because the model had to make an extra `pc_search` call as a fallback, the Recall session's input tokens rose from **37.7k to 56.4k** on Pi and from **28.3k to 42.6k** on OpenCode.

PR [#2231](https://github.com/oceanbase/powercontext/pull/2231) then mitigated the problem: the embedding waits of all retrieval paths in one context preparation (Atomic Memory and Topic Memory) now share a single budget, raised to **0.5 s** by default and configurable through a runtime setting. The PR's measurements against a real embedding provider show 60 of 60 single-Scope preparations returning context, and 36 of 40 when three Scopes are searched together; with the provider stalled for 2 s, every preparation still came back empty. And when Topic Memory needs a second embedding round trip of its own, a 0.3 s provider is enough to exhaust the 0.5 s budget and send it back to full-text search. The change makes the failure rarer; it does not eliminate it.

The lesson from this investigation is simple: **a pass rate alone is far from enough in an evaluation report**, especially when the same silent degradation can still happen under higher latency. Besides the final score, the report now breaks down each step's duration, input, output, and cached tokens, and the Server's actual generation and embedding calls.

## What this evaluation can tell you today

In the Kimi Code pilot, the comparison produced **OFF 0/2, ON 2/2**. The numbers look great, but the same report computes the 95% confidence interval of the ON success rate: **34% to 100%**. Statistically, a sample of two trials says almost nothing about the size of any improvement.

So there are only two conclusions we are willing to draw today:

1. **The end-to-end path works**: with the three kinds of leaks above narrowed, on the hosts we have run so far, the ON arm's full memory lifecycle (automatic capture, asynchronous extraction into storage, and on-demand recall in a new session) works as intended.
2. **The tasks do not obviously give the answer away**: the OFF arm answered neither pilot trial correctly, which is consistent with the tasks not handing out the context they ask about. Two trials, plus the residual channel above, are not enough to show that the OFF arm ran fully blind.

As for how many percentage points PowerContext adds to task success in real development work, these few numbers cannot answer that yet. Answering it needs a larger set of tasks closer to real engineering, enough trials, and a continued eye on the cost of every step.

If you want to run this comparison on your own host or model, the full instructions are in [`e2e/bub/README.md`](https://github.com/oceanbase/powercontext/blob/master/e2e/bub/README.md). With a PowerContext Server that enforces authentication, a single command starts it:

```bash
make harness-paired ARGS='--host pi --trials 2'
