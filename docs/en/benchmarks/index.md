---
template: benchmark.html
page_type: benchmark
title: Benchmarks
description: How PowerContext performs on long-term conversational memory and repository-level software engineering evaluations.
hide:
  - navigation
  - toc
benchmark:
  hero:
    label: Benchmark evidence
    title:
      - Context,
      - under pressure.
    lead: Two public evaluations test long-term recall and repository-level software engineering with measurable outcomes.
    actions_label: Jump to a benchmark
    actions:
      - label: See LoCoMo
        target: locomo
      - label: See SWE-bench
        target: swe-bench
    visual_label: Key results from the LoCoMo and SWE-bench Pro evaluations
    results:
      - name: LoCoMo
        value: 90.78
        decimals: 2
        suffix: "%"
        display: 90.78%
        accessible: 90.78 percent accuracy
        metric: question-answer accuracy
      - name: SWE-bench Pro
        value: 86.73
        decimals: 2
        suffix: "%"
        display: 86.73%
        accessible: 86.73 percent task resolution
        metric: tasks resolved with PowerContext on
  orientation:
    title: Two benchmarks. Two different questions.
    lead: "Memory quality matters twice: first when an agent must recover what happened, then when it must use context to finish real work."
    tests:
      - name: LoCoMo
        question: Can the system remember a long-running conversation?
        answer: It measures direct recall, temporal reasoning, multi-step reasoning, and context grounded open-domain answers.
        target: locomo
        link: Explore the memory test
      - name: SWE-bench Pro
        question: Can an agent turn context into a working patch?
        answer: It gives Codex a real repository and issue, then grades the resulting patch with executable tests.
        target: swe-bench
        link: Explore the coding test
  locomo:
    title: LoCoMo tests long-term recall.
    lead: The public dataset contains long, multi-session conversations. PowerContext is evaluated on the answerable question set, where facts can be separated by many sessions.
    facts:
      - label: Conversations
        value: "10"
      - label: Scored questions
        value: 1,540
      - label: Question types
        value: "4"
    categories_label: LoCoMo question categories used in the PowerContext result
    categories:
      - name: Single-hop
        count: "841"
        description: Recover one fact from the conversation history.
      - name: Temporal
        count: "321"
        description: Reason about dates, order, and duration across sessions.
      - name: Multi-hop
        count: "282"
        description: Connect several facts before producing an answer.
      - name: Open-domain
        count: "96"
        description: Combine conversation evidence with general knowledge.
    results_title: One run, three ways to carry context.
    results_lead: Switch metrics to compare PowerContext, PowerMem, and placing the entire conversation in the prompt.
    tabs_label: LoCoMo result metric
    metrics:
      - id: accuracy
        label: Accuracy
        callout: +37.88 points
        callout_detail: above full context
        chart_label: Accuracy comparison. Higher values are better.
        direction: Higher is better.
        rows:
          - name: PowerContext
            display: 90.78%
            scale: 90.78
          - name: PowerMem
            display: 87.79%
            scale: 87.79
          - name: Full context
            display: 52.9%
            scale: 52.9
      - id: latency
        label: Search p95
        callout: 12.4x
        callout_detail: full context took longer
        chart_label: Search p95 latency comparison. Lower values are better.
        direction: Lower is better.
        rows:
          - name: PowerContext
            display: 1.38 s
            scale: 8.06
          - name: PowerMem
            display: 1.44 s
            scale: 8.41
          - name: Full context
            display: 17.12 s
            scale: 100
      - id: tokens
        label: Answer tokens
        callout: 93.7% fewer
        callout_detail: than full context
        chart_label: Answer tokens per question comparison. Lower values are better.
        direction: Lower is better.
        rows:
          - name: PowerContext
            display: about 1.65k
            scale: 6.35
          - name: PowerMem
            display: about 0.9k
            scale: 3.46
          - name: Full context
            display: 26k
            scale: 100
    scope_title: What this result covers
    scope: The 90.78% result is 1,398 correct answers from 1,540 questions in categories 1-4. It does not claim results for LoCoMo event summarization or multimodal dialogue generation.
  swe:
    title: SWE-bench Pro tests whether context changes the patch.
    lead: Each task starts from a real codebase and issue. Codex edits the repository, and the official task tests decide whether the patch resolves the problem.
    method:
      - title: Same task set
        description: 731 public v2 repository issues in both arms.
      - title: Same model
        description: gpt-5.6-sol with medium reasoning in Codex.
      - title: Controlled switch
        description: OFF disables plugins. ON enables the installed PowerContext plugin.
    scores:
      - label: PowerContext OFF
        count: 602
        rate: 82.35% resolved
        accessible: 602 of 731 tasks resolved with PowerContext off
        kind: "off"
      - label: PowerContext ON
        count: 634
        rate: 86.73% resolved
        accessible: 634 of 731 tasks resolved with PowerContext on
        kind: "on"
    delta: "+32"
    delta_label: more tasks resolved
    delta_accessible: PowerContext on resolved 32 more tasks
    caption: In the reported paired run, PowerContext ON improved task resolution by 4.38 percentage points.
    scope_title: How to read this result
    scope: This is a PowerContext paired evaluation on a pinned SWE-bench Pro public v2 dataset, not an official leaderboard submission. Agent runs are stochastic, so the numbers describe this run rather than a universal guarantee.
  leaderboards:
    title: The public score index.
    lead: Put the published field next to PowerContext, while keeping the test rig visible.
    tabs_label: Select a benchmark leaderboard
    updated: Data checked August 31, 2026
    source_label: View source
    locomo:
      id: locomo-rankings
      tab: LoCoMo public claims
      count: 15 systems
      title: LoCoMo scores on all 1,540 questions
      lead: Only results that explicitly disclose the complete 1,540-question scope are included. Reader, judge, and answer-policy differences remain visible.
      table_label: LoCoMo public score index with 15 systems evaluated on 1,540 questions
      columns:
        rank: Rank
        system: System
        score: Score
        protocol: Published protocol
        evidence: Evidence
      note_title: One dataset scope, visible test rigs
      note: Every row explicitly reports all 1,540 scored questions. Results with an undisclosed or different question count are excluded. The remaining scores still use different readers, judges, and answer policies, so the rank is a public evidence index rather than an official LoCoMo leaderboard.
      rows:
        - rank: 1
          name: Zep
          score: 94.70%
          protocol: 1,540 questions; GPT-5.4 reader and judge
          evidence: Vendor run
          source: https://www.getzep.com/research/
        - rank: 2
          name: EverMemOS
          score: 94.50%
          protocol: 1,540 questions; lenient shared harness; precomputed retrieval
          evidence: Third-party run
          source: https://github.com/buildingjoshbetter/TrueMemory/blob/main/benchmarks/locomo/BENCHMARK_RESULTS.md
        - rank: 3
          name: XMDB
          score: 93.20%
          protocol: 1,540 questions; internally verified
          evidence: Vendor run
          source: https://xmdb.ai/memory
        - rank: 4
          name: TrueMemory Pro
          score: 93.00%
          protocol: 1,540 questions; three-run mean; lenient judge
          evidence: Open harness
          source: https://github.com/buildingjoshbetter/TrueMemory/blob/main/benchmarks/locomo/BENCHMARK_RESULTS.md
        - rank: 5
          name: Mem0
          score: 92.50%
          protocol: 1,540 questions; latest vendor evaluation rig
          evidence: Vendor run
          source: https://mem0.ai/research
        - rank: 6
          name: PowerContext
          score: 90.78%
          protocol: 1,540 questions; 1,398 correct; topical judge
          evidence: Project run
          source: https://github.com/oceanbase/powercontext#benchmarks
          highlight: true
        - rank: 7
          name: Honcho
          score: 89.90%
          protocol: 1,540 questions; current full-system result
          evidence: Vendor run
          source: https://honcho.dev/blog/blog/benchmarking-honcho
        - rank: 8
          name: Dakera
          score: 88.20%
          protocol: 1,540 questions; single pass; no LLM reranker
          evidence: Reproducible vendor run
          source: https://dakera.ai/benchmark/
        - rank: 9
          name: PowerMem
          score: 87.79%
          protocol: 1,540 questions; historical project run
          evidence: Project run
          source: https://github.com/oceanbase/powercontext#benchmarks
        - rank: 10
          name: Memvid
          score: 85.65%
          protocol: 1,540 questions; GPT-4o reader; lenient judge
          evidence: Open harness
          source: https://github.com/memvid/memvidbench
        - rank: 11
          name: Genesys
          score: 85.55%
          protocol: 1,540 questions; ten-run mean; frozen Mem0 protocol
          evidence: Certified vendor run
          source: https://genesys.astrixlabs.ai/developers/methodology
        - rank: 12
          name: Engram
          score: 84.50%
          protocol: 1,540 questions; shared lenient harness
          evidence: Third-party run
          source: https://github.com/buildingjoshbetter/TrueMemory/blob/main/benchmarks/locomo/BENCHMARK_RESULTS.md
        - rank: 13
          name: MemHQ
          score: 83.20%
          protocol: 1,540 questions; gpt-4o-mini; partial answers accepted
          evidence: Open harness
          source: https://memhq.ai/benchmark
        - rank: 14
          name: Logica Mind
          score: 72.50%
          protocol: 1,540 questions; Mem0 paper protocol
          evidence: Open harness
          source: https://huggingface.co/datasets/rovemark/locomo-benchmark-results
        - rank: 15
          name: Supermemory
          score: 65.40%
          protocol: 1,540 questions; shared lenient harness
          evidence: Third-party run
          source: https://github.com/buildingjoshbetter/TrueMemory/blob/main/benchmarks/locomo/BENCHMARK_RESULTS.md
    swe:
      id: swe-rankings
      tab: SWE-bench Pro official
      count: 25 official entries
      title: Every official public entry
      lead: Scale currently publishes 25 model runs, not 30 memory products. Official Rank (UB), resolve rate, confidence interval, and harness marker are preserved below.
      table_label: SWE-bench Pro official public leaderboard with 25 entries
      source: https://labs.scale.com/leaderboard/swe_bench_pro_public
      note_title: Why PowerContext is not ranked here
      note: Scale defines Rank (UB) as one plus the number of models whose lower confidence bound exceeds that run's upper bound; an asterisk marks mini-swe-agent. PowerContext reports a separate Codex paired A/B on the same 731-task public set. Its 86.73% is not an official submission and cannot be inserted into this ranking.
      spotlight:
        label: PowerContext paired run
        value: 86.73%
        detail: 634 of 731 resolved with PowerContext ON
        status: Separate protocol, not officially ranked
      columns:
        rank: Official rank
        system: Model
        score: Resolve rate
        provider: Provider
        harness: Harness
      harness_default: Scale run
      harness_star: mini-swe-agent
      rows:
        - rank: 1
          name: Muse Spark 1.1
          provider: Meta
          score: 61.50%
          ci: ±3.10
          star: true
        - rank: 1
          name: gpt-5.4 (xHigh)
          provider: OpenAI
          score: 59.10%
          ci: ±3.56
          star: true
        - rank: 3
          name: Muse Spark
          provider: Meta
          score: 55.00%
          ci: ±3.60
          star: true
        - rank: 3
          name: claude-opus-4-6 (thinking)
          provider: Anthropic
          score: 51.90%
          ci: ±3.61
          star: true
        - rank: 5
          name: gemini-3.1-pro (thinking)
          provider: Google
          score: 46.10%
          ci: ±3.60
          star: true
        - rank: 5
          name: claude-opus-4-5-20251101
          provider: Anthropic
          score: 45.89%
          ci: ±3.60
        - rank: 5
          name: claude-4-5-Sonnet
          provider: Anthropic
          score: 43.60%
          ci: ±3.60
        - rank: 5
          name: gemini-3-pro-preview
          provider: Google
          score: 43.30%
          ci: ±3.60
        - rank: 5
          name: claude-4-Sonnet
          provider: Anthropic
          score: 42.70%
          ci: ±3.59
        - rank: 10
          name: gpt-5-2025-08-07 (High)
          provider: OpenAI
          score: 41.78%
          ci: ±3.49
        - rank: 10
          name: gpt-5.2-codex
          provider: OpenAI
          score: 41.04%
          ci: ±3.57
        - rank: 10
          name: claude-4-5-haiku
          provider: Anthropic
          score: 39.45%
          ci: ±3.55
        - rank: 10
          name: qwen3-coder-480b-a35b
          provider: Alibaba
          score: 38.70%
          ci: ±3.55
        - rank: 14
          name: minimax-2.1
          provider: MiniMax
          score: 36.81%
          ci: ±3.55
        - rank: 14
          name: gemini-3-flash
          provider: Google
          score: 34.63%
          ci: ±3.55
        - rank: 16
          name: gpt-5.2
          provider: OpenAI
          score: 29.94%
          ci: ±2.15
        - rank: 16
          name: kimi-k2-instruct
          provider: Moonshot
          score: 27.67%
          ci: ±3.25
        - rank: 18
          name: qwen3-235b-a22b
          provider: Alibaba
          score: 21.41%
          ci: ±2.25
        - rank: 19
          name: gpt-oss-120b
          provider: OpenAI
          score: 16.20%
          ci: ±2.67
        - rank: 19
          name: deepseek-v3p2
          provider: DeepSeek
          score: 15.56%
          ci: ±2.63
        - rank: 21
          name: gemma-3-27b-it
          provider: Google
          score: 11.38%
          ci: ±2.15
        - rank: 21
          name: llama3-1-405b-instruct
          provider: Meta
          score: 11.18%
          ci: ±2.15
        - rank: 21
          name: glm-4.6
          provider: Z.ai
          score: 9.67%
          ci: ±2.15
        - rank: 24
          name: llama4-maverick-17b-instruct
          provider: Meta
          score: 5.24%
          ci: ±1.24
        - rank: 25
          name: codestral-2405
          provider: Mistral
          score: 1.51%
          ci: ±1.51
  reading:
    title: Read each result for the question it answers.
    lead: The two evaluations share a context theme, but their inputs, outputs, and graders are intentionally different.
    columns:
      dimension: Evaluation dimension
    rows:
      - dimension: What is tested
        locomo: Long-term conversational recall and reasoning
        swe: Repository-level issue resolution
      - dimension: Input
        locomo: Multi-session dialogue history and a question
        swe: A repository, an issue, and a clean task environment
      - dimension: Output
        locomo: A grounded natural-language answer
        swe: A code patch
      - dimension: Primary score
        locomo: Judge-rated answer accuracy
        swe: Official executable tests passed
  sources:
    title: Evidence and methodology
    lead: Follow the dataset, paper, harness, and published PowerContext figures from the original sources.
    items:
      - type: Paper
        label: Evaluating Very Long-Term Conversational Memory of LLM Agents
        href: https://aclanthology.org/2024.acl-long.747/
        description: The ACL 2024 paper that defines LoCoMo and its long-term memory tasks.
      - type: Dataset
        label: snap-research/locomo
        href: https://github.com/snap-research/locomo
        description: The public ten-conversation dataset and annotations.
      - type: Benchmark
        label: scaleapi/SWE-bench_Pro-os
        href: https://github.com/scaleapi/SWE-bench_Pro-os
        description: The public benchmark repository and official evaluation path.
      - type: Harness
        label: PowerContext evaluation console
        href: https://github.com/oceanbase/powercontext/tree/master/evaluation
        description: The pinned dataset, OFF and ON arms, isolated runner, and reporting contracts.
      - type: Results
        label: Published PowerContext benchmark figures
        href: https://github.com/oceanbase/powercontext#benchmarks
        description: The current project README values used on this page.
  cta:
    title: Inspect the system behind the scores.
    lead: PowerContext is open source. Review the implementation, evaluation harness, and contracts directly.
    label: View on GitHub
    href: https://github.com/oceanbase/powercontext
---
