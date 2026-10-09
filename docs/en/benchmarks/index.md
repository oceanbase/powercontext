---
title: Benchmarks
description: Results for long-term factual recall, implicit cognitive constraints, and context-assisted repository tasks.
benchmark:
  hero:
    label: Product benchmarks
    title:
      - Measure the effect
      - of context.
    lead: LoCoMo measures factual recall across long conversations. LoCoMo Plus tests whether implicit constraints are remembered and applied. SWE-bench Pro measures whether project context helps Codex resolve more repository issues.
    actions_label: Jump to a benchmark
    actions:
      - label: LoCoMo results
        target: locomo
      - label: LoCoMo Plus results
        target: locomo-plus
      - label: SWE-bench Pro results
        target: swe-bench
      - label: Methods and sources
        target: methods
    visual_label: LoCoMo accuracy plotted against search p95 latency
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
    title: Three benchmarks, three questions.
    lead: Factual recall, implicit constraints, and real tasks each test a different use of context.
    tests:
      - name: LoCoMo
        question: Can the system remember a long-running conversation?
        answer: It measures direct recall, temporal reasoning, multi-step reasoning, and context grounded open-domain answers.
        target: locomo
        link: Explore the memory test
      - name: LoCoMo Plus
        question: Can the system apply earlier implicit constraints to a new question?
        answer: It tests retention and application of causal, state, goal, and value constraints across long conversations.
        target: locomo-plus
        link: Explore the cognitive memory test
      - name: SWE-bench Pro
        question: Can an agent turn context into a working patch?
        answer: It gives Codex a real repository and issue, then grades the resulting patch with executable tests.
        target: swe-bench
        link: Explore the coding test
  locomo:
    title: "LoCoMo: retrieve the right context"
    lead: LoCoMo asks questions about long conversations split across sessions. PowerContext must find the relevant conversation evidence before producing an answer. This run covers 1,540 scored questions in categories 1 through 4.
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
    results_title: Accuracy and retrieval cost
    results_lead: We report answer accuracy, search p95 latency, and answer tokens for PowerContext, PowerMem, and full-context prompting.
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
            value: 90.78
          - name: PowerMem
            display: 87.79%
            scale: 87.79
            value: 87.79
          - name: Full context
            display: 52.9%
            scale: 52.9
            value: 52.9
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
            value: 1.38
          - name: PowerMem
            display: 1.44 s
            scale: 8.41
            value: 1.44
          - name: Full context
            display: 17.12 s
            scale: 100
            value: 17.12
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
            value: 1650
          - name: PowerMem
            display: about 0.9k
            scale: 3.46
            value: 900
          - name: Full context
            display: 26k
            scale: 100
            value: 26000
    scope_title: What this result covers
    scope: The 90.78% result is 1,398 correct answers from 1,540 questions in categories 1-4. It does not claim results for LoCoMo event summarization or multimodal dialogue generation.
  locomo_plus:
    title: "LoCoMo Plus: apply implicit memory constraints"
    lead: Even when a new question does not resemble an earlier cue, the system must retain and apply the user's state, goals, values, and causal background. These results compare native PowerContext with Jev candidate filtering across three models.
    results_title: Project-owner-supplied scores, with and without Jev
    provenance_note: These six scores were supplied by the project owner. The corresponding run manifests, summaries, protocol and model/Judge configurations, run hashes and raw outputs are not publicly available for verification. Reproducibility documentation describes the harness, not verification of these scores.
    table_label: Native PowerContext and PowerContext with Jev scores across three models
    best_label: Highest reported score
    gain_label: Largest score difference
    points_label: percentage points
    columns:
      model: Extraction & answer model
      baseline: Native PowerContext
      jev: PowerContext + Jev
      gain: Difference
    rows:
      - model: GPT-4o-mini
        baseline: 40.855
        jev: 49.78
      - model: Qwen3.7-plus
        baseline: 60.37
        jev: 68.947
      - model: GPT-4o
        baseline: 65.082
        jev: 69.28
    model_note: Each model is used for both Memory extraction and answer generation. Jev filters candidates after retrieval; it does not replace either model role.
    embedding_label: Embedding model
    embedding_model: qwen3.7-text-embedding
    embedding_dimensions: 1024 dimensions
  swe:
    title: "SWE-bench Pro: turn context into working patches"
    lead: To measure the effect of PowerContext, we ran the same Codex configuration twice on all 731 public v2 tasks. The two arms differed only in whether PowerContext was enabled.
    task_count: 731
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
    caption: The ON run resolves 634 tasks and the OFF run resolves 602. The difference is 32 tasks, or 4.38 percentage points.
    scope_title: Scope
    scope: This is a paired run on a pinned task set, not an official SWE-bench Pro submission. Agent runs are stochastic, so the scores describe these two runs only.
  leaderboards:
    title: Comparison with published results
    lead: Explore published LoCoMo and LoCoMo Plus results alongside the official SWE-bench Pro Public leaderboard. Each entry links to its evaluation configuration and source.
    tabs_label: Select a benchmark leaderboard
    updated: LoCoMo / SWE-bench Pro checked August 31, 2026
    source_label: View source
    locomo:
      id: locomo-rankings
      tab: LoCoMo
      count: 15 systems
      title: Reported LoCoMo scores
      lead: The selected systems report scores on all 1,540 questions. Reader, judge, and answer-matching choices still differ.
      table_label: LoCoMo public score index with 15 systems evaluated on 1,540 questions
      columns:
        rank: Rank
        system: System
        score: Score
        protocol: Published protocol
        evidence: Evidence
      note_title: Comparison limits
      note: Each cited result reports all 1,540 scored questions. The readers, judges, and answer policies are not standardized, so this list is not an official LoCoMo leaderboard.
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
    locomo_plus:
      tab: LoCoMo Plus
      title: Published Cognitive scores
      count: 8 published entries
      updated: Verified October 7, 2026
      table_label: Published LoCoMo Plus Cognitive scores and evaluation configurations
      score_label: Cognitive score
      spotlight:
        title: PowerContext · GPT-4o
        model: GPT-4o
        link_label: Compare all three models
      rows:
        - name: T-Mem
          score: 74.81%
          evidence: T-Mem paper · Table 3
          protocol: Cognitive; GPT-4.1-mini construction; GPT-4o answers; Gemini-2.5-Flash judge
          source: https://arxiv.org/html/2606.15405v1#S3.T3
        - name: HyperMem
          score: 48.63%
          evidence: T-Mem paper · Table 3
          protocol: Cognitive; baseline experiment in the T-Mem paper
          source: https://arxiv.org/html/2606.15405v1#S3.T3
        - name: MemOS
          score: 32.67%
          evidence: T-Mem paper · Table 3
          protocol: Cognitive; baseline experiment in the T-Mem paper
          source: https://arxiv.org/html/2606.15405v1#S3.T3
        - name: Gemini-2.5-Pro
          score: 26.06%
          evidence: LoCoMo Plus paper · Table 1
          protocol: Cognitive; full context; Gemini-2.5-Flash judge
          source: https://aclanthology.org/2026.acl-long.1150.pdf#page=6
        - name: GPT-4o
          score: 21.05%
          evidence: LoCoMo Plus paper · Table 1
          protocol: Cognitive; full context; Gemini-2.5-Flash judge
          source: https://aclanthology.org/2026.acl-long.1150.pdf#page=6
        - name: A-Mem
          score: 17.20%
          evidence: LoCoMo Plus paper · Table 1
          protocol: Cognitive; GPT-4o answers; Gemini-2.5-Flash judge
          source: https://aclanthology.org/2026.acl-long.1150.pdf#page=6
        - name: Mem0
          score: 15.80%
          evidence: LoCoMo Plus paper · Table 1
          protocol: Cognitive; GPT-4o answers; Gemini-2.5-Flash judge
          source: https://aclanthology.org/2026.acl-long.1150.pdf#page=6
        - name: SeCom
          score: 14.90%
          evidence: LoCoMo Plus paper · Table 1
          protocol: Cognitive; GPT-4o answers; Gemini-2.5-Flash judge
          source: https://aclanthology.org/2026.acl-long.1150.pdf#page=6
    swe:
      id: swe-rankings
      tab: SWE-bench Pro
      count: 25 official entries
      title: SWE-bench Pro Public leaderboard
      lead: The chart shows five official entries with their reported confidence intervals. PowerContext appears separately because its result uses a different run protocol.
      table_label: SWE-bench Pro official public leaderboard with 25 entries
      source: https://labs.scale.com/leaderboard/swe_bench_pro_public
      note_title: PowerContext is not an official entry
      note: Scale ranks official submissions using confidence bounds. PowerContext reports a separate Codex paired run on the same 731-task public set. Its 86.73% result is not an official submission and is not assigned a rank here.
      spotlight:
        label: PowerContext paired run
        value: 86.73%
        detail: 634 of 731 resolved with PowerContext ON
        status: Not an official leaderboard entry
      columns:
        rank: Rank (UB)
        system: Model
        score: Resolve rate
        provider: Provider
        harness: Harness
      rank_note: Rank (UB) is calculated from confidence intervals; overlaps produce ties and skipped positions.
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
    title: Compare what each result measures.
    lead: The two evaluations share a context theme, but their inputs, outputs, and graders are intentionally different.
    scroll_hint: Swipe sideways to compare both evaluations.
    table_label: Comparison of the LoCoMo and SWE-bench Pro evaluations
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
    title: Evaluation methods and sources
    lead: Method references and evaluation tools for all three benchmarks. Read models, scoring rules, and data scope together.
    groups:
      - id: locomo
        title: LoCoMo
        items:
          - type: Paper and task definition
            label: Evaluating Very Long-Term Conversational Memory of LLM Agents
            href: https://aclanthology.org/2024.acl-long.747/
          - type: Dataset
            label: snap-research/locomo
            href: https://github.com/snap-research/locomo
      - id: locomo-plus
        title: LoCoMo Plus
        items:
          - type: Paper and task definition
            label: Beyond-Factual Cognitive Memory Evaluation Framework
            href: https://arxiv.org/abs/2602.10715
          - type: Pinned data and official evaluator
            label: xjtuleeyf/Locomo-Plus · 059f4e3
            href: https://github.com/xjtuleeyf/Locomo-Plus/tree/059f4e3d38f7f1f96765e8e2cb7de3097551bffb
          - type: PowerContext harness and scoring notes
            label: benchmark/locomo_plus · 87fceeb7
            href: https://github.com/oceanbase/powercontext/tree/87fceeb7bbc703b6d2efa2003277e40b0fc2262b/benchmark/locomo_plus
      - id: swe
        title: SWE-bench Pro
        items:
          - type: Task set and grader
            label: scaleapi/SWE-bench_Pro-os
            href: https://github.com/scaleapi/SWE-bench_Pro-os
          - type: Run configuration
            label: PowerContext evaluation console
            href: https://github.com/oceanbase/powercontext/tree/master/evaluation
  cta:
    title: Use PowerContext in your workflow
    lead: Start with a supported agent or connect an application through the HTTP API. The repository contains the runtime, integrations, and evaluation tools.
    label: Open the GitHub repository
    href: https://github.com/oceanbase/powercontext
---
