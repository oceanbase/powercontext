# Coding evaluations

Coding suites measure whether an agent can complete repository tasks and preserve the context needed to continue work.

- [SWE-bench Pro](swebench_pro/README.md): pinned public tasks, gold-patch validation, PowerContext OFF/ON runs,
  official grading, and task reports.
- [Work continuity](work_continuity/README.md): recorded continuation attempts, context assembly, quality scoring,
  and comparisons between handoff and compaction methods, exposed through `work-continuity`.

The shared Python environment lives in [evaluation](../README.md). SWE-bench Pro provides the `swebench-pro` command
and owns its
[Web console, worker, execution tools, and deployment configuration](swebench_pro/docs/console.md).
Keep each coding suite's runtime and results with that suite.
