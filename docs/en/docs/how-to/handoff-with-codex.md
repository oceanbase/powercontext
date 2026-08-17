---
title: Hand off work in Codex
description: Explicitly transfer the verifiable state of current work to another task, session, or model.
---

# Hand off work in Codex

Use a Handoff to explicitly transfer current work to another Codex task, later session, or model. When you finish, the
receiver gets inspected temporary content that includes the objective, verified progress, blockers, next action, and
evidence.

## Before you start

Complete [Install and run](install-and-run.md), keep the Server running, and start a Codex session with the
PowerContext plugin configured in the current project. Handoff content belongs to the current project scope. The
receiving task should continue in that project or receive the complete Prepared Handoff.

## 1. State the handoff boundary

Tell Codex the boundary of the transfer and what the receiver needs. For example:

> Prepare a PowerContext Handoff for this task. Record the objective, verified progress, current blockers, and next
> action; inspect every statement in the draft for evidence, then give the completed handoff to the next task.

Include the current task's objective, verified progress, blockers, and next action. Do not put secrets, access tokens,
or other sensitive information in the handoff.

## 2. Inspect the handoff draft

Codex captures a concise current-state Source and activates the Handoff. Inspect the generated Draft and correct
missing, stale, or unsupported statements. A repeated boundary can return `ignored`, which means that the Source has
already been used.

## 3. Give it to the receiving task

After the inspection, Codex finalizes the Handoff. Give the resulting Prepared Handoff to the receiving task unchanged.
The receiver treats it as untrusted history, checks it against the current repository, user request, and system
instructions, then continues the work.

## 4. Make it durable only when needed

Handoff Drafts and Prepared Handoffs are temporary carriers and do not automatically become durable project knowledge.
Ask Codex to commit a Handoff only when the user explicitly wants to preserve a milestone. Use Memory for durable,
searchable decisions, constraints, state, or next steps. Read [Memory and Handoff](../explanation/memory-and-handoff.md)
for the distinction.
