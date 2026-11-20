---
title: "Improving Power-System Quality and Efficiency: Reworking Domain Agents with PowerContext"
description: "A local deployment perspective on using LangGraph, Context Manager, Memory Manager, and Task Manager to make power-diagnostics agents more traceable, reusable, and resilient."
slug: power-system-agent-memory
date: 2026-11-11
authors:
  - beaten
categories:
  - Engineering
cover: assets/blog/power-system-agent-memory/cover.png
cover_alt: A power-system agent architecture connecting diagnostic agents, memory, and persistent task state.
draft: false
pin: false
---

> A power-system agent must do more than complete one question or one diagnosis. Faced with changing operating data, complex fault-analysis procedures, and tasks that continue across shifts, we organize an Agent Runtime around LangGraph: LangGraph orchestrates workflows, Context Manager manages the context for the current inference, Memory Manager preserves reusable experience, and Task Manager manages task state and recovery.

## 01 Why do power-system agents need Memory?

The power-intelligence platform we are building supports operational analysis and fault diagnosis for a port power system. When a user selects an agent, the system calls a local model, knowledge services, databases, and domain algorithms to run a multi-step analysis and produce diagnostic conclusions, charts, and reports.

This is different from ordinary chat. A fault diagnosis may first read telemetry and status signals, then query equipment topology, historical alarms, and relevant procedures. During analysis it produces candidate causes, eliminated hypotheses, and new data requirements. After the task ends, the diagnosis and maintenance outcome may become important evidence for the next analysis.

If every task starts from zero, historical cases remain trapped in logs. If all history is placed in the prompt, context becomes redundant, data can be stale, and facts can be confused with inferences. If a service restarts halfway through a task, simply running the whole analysis again is also unsafe.

We therefore consider Agent Memory as part of the complete Agent Runtime and focus on three questions:

- **Context:** What should this inference see now?
- **Memory:** Which facts and experiences from the past are worth keeping and reusing?
- **Task:** Where is the current task, and how can it continue accurately after interruption?

These responsibilities are different, but they cooperate within one diagnosis. LangGraph remains the workflow engine, while Context Manager, Memory Manager, and Task Manager are organized around it.

## 02 The existing system: LangGraph orchestrates six power agents

The platform currently contains six business agents:

| Agent | Main responsibility |
|---|---|
| Risk Analysis Agent | General risk analysis for the power system |
| Current Fault Diagnosis Agent | Read telemetry and status data, cluster signals, organize evidence, classify faults, and generate reports |
| Power Quality Risk Agent | Analyze power-quality events, power correlations, and harmonics |
| Resonance Risk Agent | Check harmonic resonance |
| Cable Insulation Risk Agent | Assess insulation risk from partial discharge, temperature, and insulation resistance |
| System Operation Risk Agent | Analyze operating risk and recommend actions from snapshots, alarms, and topology |

The diagnostic backend uses LangGraph to organize multi-node flows. Each agent contains a State, Graph, and Nodes layer:

- **State** defines the data structure carried through the flow;
- **Graph** assembles nodes, edges, and conditional routes;
- **Nodes** perform data access, analysis, knowledge retrieval, and report generation.

MCP subservices provide access to databases, the RAGFlow knowledge base, standards and procedures, cable data, and resonance checks. The model is a locally deployed Qwen model, while BGE-M3 provides embeddings. RAGFlow and a local Wiki provide knowledge retrieval; load forecasting and partial-discharge recognition are handled by independent domain algorithm services. The backend streams workflow steps, diagnostic results, charts, and report content to the Vue frontend over SSE.

This stack already covers the main capabilities required to run the business agents. We keep LangGraph as the workflow core and organize Memory capabilities on top of the existing architecture rather than introducing another orchestration framework.

## 03 Overall architecture: LangGraph orchestrates; the Runtime manages context, memory, and tasks

<figure class="fig" id="fig-power-system-agent-architecture">
<div class="fig-canvas" style="--fw:960;--fh:580" role="img" aria-label="LangGraph multi-agent architecture for a power system">
<svg viewBox="0 0 960 580">
<path class="fig-wire" d="M565 110 L565 154" marker-end="url(#fig-arrow)"/>
<path class="fig-wire" d="M366 205 L394 205" marker-end="url(#fig-arrow)" marker-start="url(#fig-arrow)"/>
<path class="fig-wire" d="M766 205 L794 205" marker-end="url(#fig-arrow)" marker-start="url(#fig-arrow)"/>
<path class="fig-wire accent" d="M580 250 L580 294" marker-end="url(#fig-arrow-accent)" marker-start="url(#fig-arrow-accent)"/>
<path class="fig-wire" d="M565 470 L565 436" marker-end="url(#fig-arrow)"/>
</svg>
<span class="fig-label strong" style="--x:0;--y:30">Application</span>
<span class="fig-label muted small" style="--x:0;--y:52">Power-system agents</span>
<span class="fig-label strong" style="--x:0;--y:180">Orchestration</span>
<span class="fig-label muted small" style="--x:0;--y:202">Workflows and coordination</span>
<span class="fig-label strong" style="--x:0;--y:330">Runtime</span>
<span class="fig-label muted small" style="--x:0;--y:352">Context, memory and tasks</span>
<span class="fig-label strong" style="--x:0;--y:500">Infrastructure</span>
<span class="fig-label muted small" style="--x:0;--y:522">Data, knowledge and models</span>
<div class="fig-panel" style="--x:170;--y:0;--w:790;--h:110"></div>
<div class="fig-box" style="--x:186;--y:20;--w:116;--h:70"><b>Risk analysis</b></div>
<div class="fig-box" style="--x:312;--y:20;--w:116;--h:70"><b>Fault diagnosis</b></div>
<div class="fig-box" style="--x:438;--y:20;--w:116;--h:70"><b>Power quality</b></div>
<div class="fig-box" style="--x:564;--y:20;--w:116;--h:70"><b>Resonance</b></div>
<div class="fig-box" style="--x:690;--y:20;--w:116;--h:70"><b>Cable insulation</b></div>
<div class="fig-box" style="--x:816;--y:20;--w:116;--h:70"><b>Operations</b></div>
<div class="fig-box dashed" style="--x:170;--y:152;--w:190;--h:106"><b>MCP tools</b><span>Database · knowledge base</span><span>Domain tools · services</span></div>
<div class="fig-box accent" style="--x:400;--y:160;--w:360;--h:90"><b>LangGraph</b><span>Workflow orchestration and agent coordination</span></div>
<div class="fig-box dashed" style="--x:800;--y:152;--w:160;--h:106"><b>Frontend (SSE)</b><span>Process flow · reports</span><span>Analytics · PDF export</span></div>
<div class="fig-panel accent" style="--x:170;--y:300;--w:790;--h:130"><header><b>PowerContext</b></header></div>
<div class="fig-card" style="--x:186;--y:344;--w:246;--h:74"><b>Context Manager</b><span>Build context · manage evidence</span><span>Dynamic retrieval</span></div>
<div class="fig-card" style="--x:442;--y:344;--w:246;--h:74"><b>Memory Manager</b><span>Facts · experience</span><span>Validation and lifecycle</span></div>
<div class="fig-card" style="--x:698;--y:344;--w:246;--h:74"><b>Task Manager</b><span>Lifecycle · persistence</span><span>Recovery · idempotency</span></div>
<div class="fig-panel" style="--x:170;--y:470;--w:790;--h:100"></div>
<div class="fig-box fill" style="--x:186;--y:486;--w:142;--h:68"><b>RAGFlow</b><span>Private knowledge</span></div>
<div class="fig-box fill" style="--x:338;--y:486;--w:142;--h:68"><b>Local wiki</b><span>Expert knowledge</span></div>
<div class="fig-box fill" style="--x:490;--y:486;--w:142;--h:68"><b>Vector database</b><span>BGE-M3</span></div>
<div class="fig-box fill" style="--x:642;--y:486;--w:142;--h:68"><b>Local LLM</b><span>Qwen</span></div>
<div class="fig-box fill" style="--x:794;--y:486;--w:142;--h:68"><b>Domain services</b><span>Analytics and models</span></div>
</div>
<figcaption>Figure 1. The Agent Runtime for a power system. LangGraph orchestrates business agents and calls context, memory, and task capabilities; the lower layers connect local models, knowledge services, data, and domain algorithms.</figcaption>
</figure>

The architecture has four layers.

**First, business agents.** Risk analysis, current-fault, power-quality, resonance, cable-insulation, and system-operation agents carry the specific business tasks.

**Second, workflow orchestration.** LangGraph executes nodes, routes conditions, and advances workflow state. MCP connects databases, knowledge bases, standards, and domain tools; SSE sends progress and results to the frontend.

**Third, the Agent Runtime.** Context Manager, Memory Manager, and Task Manager handle current inference context, cross-task memory, and the business task lifecycle.

**Fourth, infrastructure.** A local Qwen model provides inference, BGE-M3 provides vector representations, RAGFlow and Wiki provide knowledge retrieval, databases hold operational data, and domain services perform professional numerical analysis.

The boundary is important: **LangGraph manages the flow, while the three Managers manage domain state and data.** LangGraph decides which node runs next; Context Manager decides what the current model call needs; Memory Manager decides which past experience is reusable; Task Manager maintains task lifecycle, results, and recovery.

They are complementary responsibilities. LangGraph calls and coordinates them from within workflow nodes.

## 04 Context Manager: prepare the context before every inference

Power-diagnosis data comes from many sources: real-time telemetry, status signals, alarms, equipment records, topology, historical cases, procedures, and hypotheses produced during analysis. If these are simply concatenated into a long prompt, the model has difficulty distinguishing current facts, historical information, and inference.

We therefore represent context as a structured object rather than an ever-growing conversation.

### 4.1 What belongs in context?

A diagnosis context mainly contains:

- the task goal, task identifier, and current stage;
- confirmed facts;
- diagnostic hypotheses that still need verification;
- key evidence and reference IDs;
- completed steps and open work;
- relevant knowledge, historical memory, and constraints;
- the token budget for the current model call.

A simplified representation is:

```python
class AgentContext(TypedDict):
    task_id: str
    task_goal: str
    task_status: str

    conversation_summary: str
    current_step: str

    confirmed_facts: list
    diagnosis_hypotheses: list
    evidence_refs: list

    completed_steps: list
    pending_steps: list

    knowledge_refs: list
    constraints: list
    token_budget: int
```

The key is to keep **facts and inferences separate**. “The temperature at a measurement point was 85.2 °C” is an observed fact with a data source and timestamp. “The equipment may have a cooling problem” is a diagnostic hypothesis that requires further verification. They have different positions and statuses in context, so repeated model reasoning does not silently turn a hypothesis into a fact.

### 4.2 Connect conclusions to original data with evidence IDs

We do not store only natural-language descriptions generated by the model. We associate them with original data through `evidence_id`. An evidence object includes its source, equipment, collection time, data type, value, quality, and validity period:

```json
{
  "evidence_id": "EV-001",
  "source": "SCADA",
  "equipment_id": "TRANSFORMER-01",
  "timestamp": "2026-10-30T10:30:00+08:00",
  "data_type": "temperature",
  "value": 85.2,
  "unit": "°C",
  "quality": "valid",
  "valid_until": "2026-10-30T10:35:00+08:00"
}
```

The model context keeps an evidence summary and reference ID; complete original data stays in external storage. When a report is generated, the evidence can be read back by reference. When a task resumes, the system can check the evidence window and validity.

This matters especially in power systems: operating data is time-sensitive, and data that was valid yesterday may not support today's conclusion.

### 4.3 How should context be selected and compressed?

More context is not always better. We consider four dimensions when building context:

1. **Task relevance:** Does it directly serve the current diagnosis?
2. **Temporal validity:** Is the data still within its valid period?
3. **Trust:** Is the source reliable, and has the memory been verified?
4. **Diagnostic value:** Does it distinguish candidate causes or determine the next action?

Retrieval combines metadata rules, keyword search, and vector search. Assembly then allocates the available budget by importance, instead of truncating blindly and losing key evidence.

Context compression uses a structured summary: it preserves intermediate conclusions, key evidence references, eliminated causes, and open questions while retaining an index to the original evidence. **A summary stores conclusions and process references; it cannot replace the original evidence.**

LangGraph State stores only the state, summaries, and external references needed by the current workflow. It does not accumulate every historical tool result. Before each LLM node runs, Context Manager assembles the model input again from the current task state.

## 05 Memory Manager: turn repeated diagnoses into reusable experience

Context Manager answers “what should this inference see?” Memory Manager answers “what is worth preserving across tasks, and how can the next task use it?”

We do not treat Memory as a complete log or a copy of every conversation. The useful material is organized, traceable, and reusable in similar tasks.

<figure class="fig" id="fig-memory">
<div class="fig-canvas" style="--fw:960;--fh:330" role="img" aria-label="Memory extraction, validation, and reuse from operational evidence">
<svg viewBox="0 0 960 330">
<path class="fig-wire" d="M210 70 L244 70" marker-end="url(#fig-arrow)"/>
<path class="fig-wire" d="M460 70 L494 70" marker-end="url(#fig-arrow)"/>
<path class="fig-wire" d="M710 70 L744 70" marker-end="url(#fig-arrow)"/>
<path class="fig-wire accent" d="M855 116 L855 160 Q855 170 845 170 L510 170 Q500 170 500 180 L500 214" marker-end="url(#fig-arrow-accent)"/>
<path class="fig-wire dashed" d="M290 260 L115 260 Q105 260 105 250 L105 122" marker-end="url(#fig-arrow)"/>
</svg>
<span class="fig-label muted small" style="--x:0;--y:0">Source</span>
<div class="fig-box start" style="--x:0;--y:24;--w:210;--h:92"><b>Historical tasks</b><span>SCADA · alarms · records</span></div>
<span class="fig-label muted small" style="--x:250;--y:0">Extract</span>
<div class="fig-box start" style="--x:250;--y:24;--w:210;--h:92"><b>Atomic memories</b><span>Facts · actions · results</span></div>
<span class="fig-label muted small" style="--x:500;--y:0">Validate</span>
<div class="fig-box start" style="--x:500;--y:24;--w:210;--h:92"><b>Evidence review</b><span>Rules · experts · outcomes</span></div>
<span class="fig-label muted small" style="--x:750;--y:0">Persist</span>
<div class="fig-box start accent" style="--x:750;--y:24;--w:210;--h:92"><b>Verified memory</b><span>Versioned · scoped · reusable</span></div>
<div class="fig-box" style="--x:290;--y:220;--w:420;--h:80"><b>Retrieval and experience reuse</b><span>Metadata filters · hybrid search · relevance ranking</span></div>
<span class="fig-label muted small" style="--x:115;--y:172">Feedback: a new diagnosis</span>
</div>
</figure>

### 5.1 Short-term and long-term memory

**Short-term memory** serves the current task. It records the goal, execution progress, collected-data references, current hypotheses, verification results, eliminated causes, and open work. It changes as the task advances and works with LangGraph State and Checkpointer to keep the workflow continuous.

**Long-term memory** is shared across tasks. It stores historical faults and maintenance outcomes, equipment signatures, typical fault patterns, verified diagnostic paths, expert rules, and professional handling experience.

The boundary is clear: short-term memory follows the current task; long-term memory can be retrieved and reused by other tasks.

### 5.2 Fact Memory and Experience Memory

Within long-term memory, we distinguish fact and experience:

| Type | Stored content | Example |
|---|---|---|
| Fact Memory | Confirmed objective facts | A maintenance inspection confirmed a cooling-fan failure |
| Experience Memory | Scenario, action, result, and reusable lesson | When temperature rise and vibration occur together, check the cooling system first |

Experience memory uses a scenario–action–result–lesson structure:

```json
{
  "memory_id": "MEM-001",
  "type": "experience",
  "scenario": "Temperature rise and vibration are abnormal under a specific load",
  "action": "Check the cooling system and related alarms",
  "result": "Maintenance confirmed a cooling-fan failure",
  "lesson": "This combination is a useful lead for investigating a cooling-system fault",
  "source_ids": ["EV-001", "WORKORDER-009"],
  "confidence": 0.92,
  "status": "Verified"
}
```

Each memory is an atomic unit that can be retrieved, updated, merged, or retired independently. `source_ids` keep it connected to original evidence or maintenance records.

### 5.3 Extract and validate memories

After a task is complete, Memory Extractor reads task records, diagnostic conclusions, and maintenance outcomes to produce candidate memories. Memory Validator checks them against data, rules, historical cases, and final outcomes. Content that cannot be confirmed automatically enters a Review Inbox, where specialists can add evidence or review it.

<figure class="fig" id="fig-memory-validation">
<div class="fig-canvas" style="--fw:960;--fh:396" role="img" aria-label="Memory validation: extraction, evidence checks, expert review, and reuse">
<svg viewBox="0 0 960 396">
<path class="fig-wire" d="M210 35 L244 35" marker-end="url(#fig-arrow)"/>
<path class="fig-wire" d="M460 35 L494 35" marker-end="url(#fig-arrow)"/>
<path class="fig-wire" d="M710 35 L744 35" marker-end="url(#fig-arrow)"/>
<path class="fig-wire muted" d="M890 70 L890 154" marker-end="url(#fig-arrow-muted)"/>
<path class="fig-wire" d="M780 70 L780 102 Q780 112 770 112 L615 112 Q605 112 605 122 L605 154" marker-end="url(#fig-arrow)"/>
<path class="fig-wire accent" d="M830 70 L830 343 Q830 353 820 353 L716 353" marker-end="url(#fig-arrow-accent)"/>
<path class="fig-wire" d="M605 226 L605 314" marker-end="url(#fig-arrow)"/>
<path class="fig-wire" d="M500 353 L466 353" marker-end="url(#fig-arrow)"/>
<path class="fig-wire" d="M250 353 L216 353" marker-end="url(#fig-arrow)"/>
</svg>
<div class="fig-box" style="--x:0;--y:0;--w:210;--h:70"><b>Historical tasks and diagnosis records</b></div>
<div class="fig-box" style="--x:250;--y:0;--w:210;--h:70"><b>Memory extractor</b></div>
<div class="fig-box" style="--x:500;--y:0;--w:210;--h:70"><b>Candidate memory</b></div>
<div class="fig-box accent" style="--x:750;--y:0;--w:210;--h:70"><b>Memory validator</b></div>
<div class="fig-box quiet dashed" style="--x:820;--y:160;--w:140;--h:66"><b>Rejected</b><span>Validation failed</span></div>
<div class="fig-box" style="--x:500;--y:160;--w:210;--h:66"><b>Review inbox</b><span>Insufficient evidence</span></div>
<div class="fig-box accent" style="--x:500;--y:320;--w:210;--h:66"><b>Verified memory</b><span>Sufficient or reviewed</span></div>
<div class="fig-box" style="--x:250;--y:320;--w:210;--h:66"><b>Memory store</b></div>
<div class="fig-box" style="--x:0;--y:320;--w:210;--h:66"><b>Future diagnosis retrieval</b></div>
<span class="fig-label muted small" style="--x:615;--y:262">Expert review or more evidence</span>
</div>
</figure>

Memories have explicit lifecycle states:

| State | Meaning | Used in normal diagnosis retrieval |
|---|---|---|
| Candidate | Newly extracted candidate | No |
| Pending | Waiting for validation or review | No by default |
| Verified | Passed validation and can be reused as trusted experience | Yes |
| Rejected | Failed validation or was rejected in review | No |
| Retired | Expired, superseded, or no longer applicable | No |

Validation is not the extracting LLM declaring itself correct. Memory Validator checks whether the memory is supported by real operating data, is consistent with equipment logic and professional rules, has similar cases, and was confirmed by later maintenance results. Each validation records its evidence, rule version, time, and outcome. For high-risk knowledge, specialist review and authoritative business outcomes are important sources of trust.

### 5.4 How is memory retrieved?

For a new task, Memory Manager narrows the scope with metadata such as equipment type, equipment ID, fault type, region, operating condition, and time. It then combines keyword and vector similarity to retrieve relevant memories.

Ranking considers semantic relevance, evidence completeness, verification state, and validity period. Normal diagnostic context includes only Verified memories that are still valid. Pending content remains in the review process and is not mixed with trusted experience.

### 5.5 What PowerContext suggests

PowerContext treats agent context and memory as independent engineering objects instead of hiding them in conversation history. For power diagnostics, we take inspiration from these design ideas:

| PowerContext idea | Design for power-system agents |
|---|---|
| Source and Memory separation | Operational data, alarms, and maintenance records are sources; extracted memories are stored independently |
| Atomic Memory | Each memory can be retrieved, updated, merged, or retired independently |
| Memory / Experience | Separate confirmed facts from scenario–action–result experience |
| Review Inbox | Candidate memories enter the trusted store only after validation or expert review |
| Prepared Context | Filter, rank, and budget recalled memories before passing them to the model |
| Scope | Organize memories by global, region, station, or equipment scope |
| Handoff | Preserve work handed across shifts or agents |

Scope organizes memory and retrieval boundaries. It does not replace authentication or authorization.

## 06 Task Manager: continue after interruption

A power diagnosis is not always a short request. “A 110 kV bus at a substation has an abnormal voltage; analyze the cause and generate a report” may require data collection, topology queries, alarm correlation, fault-hypothesis analysis, waiting for additional data, result verification, and report generation. It may last minutes or hours and cross several shifts.

We therefore keep task management as a separate concern. Task Manager maintains the business task lifecycle and works with LangGraph workflow state.

### 6.1 Task lifecycle state machine

The task lifecycle can be described as:

<figure class="fig" id="fig-task-lifecycle">
<div class="fig-canvas" style="--fw:960;--fh:410" role="img" aria-label="Task lifecycle with waiting, recovery, completion, and failure states">
<svg viewBox="0 0 960 410">
<path class="fig-wire" d="M40 48 L84 48" marker-end="url(#fig-arrow)"/>
<path class="fig-wire" d="M270 48 L384 48" marker-end="url(#fig-arrow)"/>
<path class="fig-wire" d="M410 76 L410 106 Q410 116 400 116 L160 116 Q150 116 150 126 L150 230" marker-end="url(#fig-arrow)"/>
<path class="fig-wire" d="M210 236 L210 176 Q210 166 220 166 L430 166 Q440 166 440 156 L440 82" marker-end="url(#fig-arrow)"/>
<path class="fig-wire" d="M550 76 L550 106 Q550 116 560 116 L800 116 Q810 116 810 126 L810 230" marker-end="url(#fig-arrow)"/>
<path class="fig-wire" d="M750 236 L750 176 Q750 166 740 166 L530 166 Q520 166 520 156 L520 82" marker-end="url(#fig-arrow)"/>
<path class="fig-wire accent" d="M480 76 L480 324" marker-end="url(#fig-arrow-accent)"/>
<path class="fig-wire" d="M570 358 L634 358" marker-end="url(#fig-arrow)"/>
</svg>
<span class="fig-dot" style="--x:20;--y:38;--w:20;--h:20"></span>
<div class="fig-box" style="--x:90;--y:20;--w:180;--h:56"><b>CREATED</b></div>
<div class="fig-box accent" style="--x:390;--y:20;--w:180;--h:56"><b>RUNNING</b></div>
<div class="fig-box" style="--x:60;--y:236;--w:180;--h:56"><b>WAITING</b></div>
<div class="fig-box" style="--x:720;--y:236;--w:180;--h:56"><b>FAILED</b></div>
<div class="fig-box" style="--x:390;--y:330;--w:180;--h:56"><b>COMPLETED</b></div>
<span class="fig-dot end" style="--x:640;--y:346;--w:24;--h:24"></span>
<span class="fig-label muted small center" style="--x:280;--y:94">Waiting for data or human input</span>
<span class="fig-label muted small center" style="--x:325;--y:174">Condition met or human resumes</span>
<span class="fig-label muted small center" style="--x:680;--y:94">Execution failed</span>
<span class="fig-label muted small center" style="--x:635;--y:174">Recovery conditions met</span>
<span class="fig-label accent small" style="--x:490;--y:260">Task complete</span>
</div>
</figure>

We distinguish two kinds of state:

- **Task lifecycle state:** CREATED, RUNNING, WAITING, COMPLETED, and FAILED describe the business task;
- **Workflow execution state:** `collect_data`, `analyze_fault`, or `verify_hypothesis` describe the current LangGraph node.

A task can be RUNNING while it is waiting for an external data interface rather than executing an LLM node. Keeping these states separate prevents business status and graph execution status from being conflated.

### 6.2 LangGraph moves workflow state forward

LangGraph State, Checkpointer, Interrupt, and Conditional Edge provide the core execution mechanisms:

- **State:** pass the goal, current stage, hypotheses, evidence references, and open work between nodes;
- **Checkpointer:** persist workflow state and find the execution thread through `thread_id`;
- **Interrupt:** pause when additional information or approval is needed, then resume when conditions are met;
- **Conditional Edge:** choose the next route based on data completeness, diagnosis, and approval state.

`task_id` and `thread_id` have different jobs. `task_id` identifies the business diagnosis; `thread_id` identifies the LangGraph workflow thread. They are mapped, but they are not the same concept.

State holds the information the current flow needs: goal, step, hypotheses, key results, and evidence IDs. Large raw datasets and complete tool results stay in external storage so State does not grow without bound.

### 6.3 Checkpointer, Task Store, Evidence Store, and Operation Store

Recovery needs to know both where execution stopped and what the step produced. We therefore separate task data:

| Store | Contents | Purpose |
|---|---|---|
| Checkpointer | LangGraph State and node position | Recover the workflow |
| Task Store | Task ID, lifecycle, progress, time, and errors | Manage the business task |
| Evidence Store | Original evidence, collection results, and metadata | Reconstruct the basis for the task |
| Operation Store | External operations, status, result, and idempotency key | Prevent duplicate execution |

If data collection completed but the service failed during later analysis, recovery needs more than “data collection is complete.” It needs the `evidence_id`, data window, and source of the result.

### 6.4 Why do we need an Operation Store?

Checkpoint state and external operations are not automatically consistent.

Suppose an agent sends an alarm to an external system. The external system succeeds, but the agent crashes before saving a new checkpoint. On recovery, the workflow may execute the alarm node again and create a duplicate operation.

For tools with external side effects, Operation Store records operation status and an idempotency key. Recovery checks the operation record before deciding whether to retry. Operations with business impact also require authorization and, when necessary, human approval; their execution remains auditable.

This connects workflow state with external operation results instead of assuming that a checkpoint alone proves whether an operation succeeded.

### 6.5 Recheck data validity during recovery

Grid operating data changes continuously. If an agent queried a line current yesterday and resumes today, yesterday's value may no longer describe the current operating condition.

During recovery, the system checks whether original data is still valid, whether equipment state has changed, whether topology and operating mode have changed, and whether the original hypothesis still holds. It collects fresh data when necessary before continuing.

**Recovery restores more than a program position; it restores the task's business meaning and currently valid evidence.**

<figure class="fig" id="fig-task-recovery">
<div class="fig-canvas" style="--fw:960;--fh:372" role="img" aria-label="Persistent task and recovery flow, including checkpoints, operation storage, and evidence storage">
<svg viewBox="0 0 960 372">
<path class="fig-wire" d="M172 35 L191 35" marker-end="url(#fig-arrow)"/>
<path class="fig-wire" d="M369 35 L388 35" marker-end="url(#fig-arrow)"/>
<path class="fig-wire" d="M566 35 L585 35" marker-end="url(#fig-arrow)"/>
<path class="fig-wire" d="M763 35 L782 35" marker-end="url(#fig-arrow)"/>
<path class="fig-wire accent dashed" d="M677 70 L677 95 Q677 105 667 105 L490 105 Q480 105 480 115 L480 134" marker-end="url(#fig-arrow-accent)"/>
<path class="fig-wire" d="M480 216 L480 246 Q480 256 470 256 L150 256 Q140 256 140 266 L140 296" marker-end="url(#fig-arrow)"/>
<path class="fig-wire" d="M480 216 L480 296" marker-end="url(#fig-arrow)"/>
<path class="fig-wire" d="M480 216 L480 246 Q480 256 490 256 L810 256 Q820 256 820 266 L820 296" marker-end="url(#fig-arrow)"/>
</svg>
<div class="fig-box" style="--x:0;--y:0;--w:172;--h:70"><b>CREATED</b><span>Task ID</span></div>
<div class="fig-box" style="--x:197;--y:0;--w:172;--h:70"><b>RUNNING</b><span>LangGraph</span></div>
<div class="fig-box" style="--x:394;--y:0;--w:172;--h:70"><b>WAITING</b><span>Human or data</span></div>
<div class="fig-box" style="--x:591;--y:0;--w:172;--h:70"><b>RESUMED</b><span>State restored</span></div>
<div class="fig-box solid" style="--x:788;--y:0;--w:172;--h:70"><b>COMPLETED</b><span>Verified result</span></div>
<div class="fig-box accent" style="--x:240;--y:140;--w:480;--h:76"><b>Recovery and validation</b><span>Restore the checkpoint · revalidate evidence · check operations</span></div>
<div class="fig-box fill" style="--x:0;--y:302;--w:280;--h:66"><b>Checkpointer</b><span>Workflow state</span></div>
<div class="fig-box fill" style="--x:340;--y:302;--w:280;--h:66"><b>Operation store</b><span>Idempotency and audit</span></div>
<div class="fig-box fill" style="--x:680;--y:302;--w:280;--h:66"><b>Evidence store</b><span>Data and provenance</span></div>
</div>
</figure>

## 07 How the three Managers cooperate in one diagnosis

Putting the design together, one diagnosis follows this flow:

<figure class="fig" id="fig-diagnosis-workflow">
<div class="fig-canvas" style="--fw:960;--fh:586" role="img" aria-label="Diagnosis workflow connecting context, memory, evidence, and task management">
<svg viewBox="0 0 960 586">
<path class="fig-wire" d="M210 33 L244 33" marker-end="url(#fig-arrow)"/>
<path class="fig-wire" d="M460 33 L494 33" marker-end="url(#fig-arrow)"/>
<path class="fig-wire" d="M710 33 L744 33" marker-end="url(#fig-arrow)"/>
<path class="fig-wire" d="M210 423 L244 423" marker-end="url(#fig-arrow)"/>
<path class="fig-wire" d="M460 423 L494 423" marker-end="url(#fig-arrow)"/>
<path class="fig-wire" d="M710 423 L744 423" marker-end="url(#fig-arrow)"/>
<path class="fig-wire" d="M460 293 L494 293" marker-end="url(#fig-arrow)"/>
<path class="fig-wire" d="M750 163 L716 163" marker-end="url(#fig-arrow)"/>
<path class="fig-wire" d="M500 163 L466 163" marker-end="url(#fig-arrow)"/>
<path class="fig-wire" d="M250 163 L216 163" marker-end="url(#fig-arrow)"/>
<path class="fig-wire" d="M897 66 L897 124" marker-end="url(#fig-arrow)"/>
<path class="fig-wire" d="M855 456 L855 514" marker-end="url(#fig-arrow)"/>
<path class="fig-wire" d="M52 196 L52 384" marker-end="url(#fig-arrow)"/>
<path class="fig-wire" d="M158 196 L158 282 Q158 292 168 292 L244 292" marker-end="url(#fig-arrow)"/>
<path class="fig-wire dashed" d="M710 292 L720 292 Q730 292 730 282 L730 107 Q730 97 740 97 L803 97 Q813 97 813 87 L813 70" marker-end="url(#fig-arrow)"/>
</svg>
<div class="fig-box" style="--x:0;--y:0;--w:210;--h:66"><b>User request</b></div>
<div class="fig-box" style="--x:250;--y:0;--w:210;--h:66"><b>Task Manager</b><span>Create or load the task</span></div>
<div class="fig-box" style="--x:500;--y:0;--w:210;--h:66"><b>LangGraph</b><span>Route to the agent workflow</span></div>
<div class="fig-box accent" style="--x:750;--y:0;--w:210;--h:66"><b>Context Manager</b><span>Build the context</span></div>
<div class="fig-box" style="--x:750;--y:130;--w:210;--h:66"><b>Domain nodes</b><span>Query data · MCP tools</span></div>
<div class="fig-box" style="--x:500;--y:130;--w:210;--h:66"><b>Evidence store</b><span>Persist evidence</span></div>
<div class="fig-box" style="--x:250;--y:130;--w:210;--h:66"><b>LLM and domain analysis</b></div>
<div class="fig-box round dashed" style="--x:0;--y:130;--w:210;--h:66"><b>Need more data or approval?</b></div>
<div class="fig-box" style="--x:250;--y:260;--w:210;--h:66"><b>Task Manager</b><span>WAITING · interrupt</span></div>
<div class="fig-box" style="--x:500;--y:260;--w:210;--h:66"><b>Resume and revalidate</b></div>
<div class="fig-box accent" style="--x:0;--y:390;--w:210;--h:66"><b>Context Manager</b><span>Assemble the report context</span></div>
<div class="fig-box" style="--x:250;--y:390;--w:210;--h:66"><b>Generate the diagnosis report</b></div>
<div class="fig-box accent" style="--x:500;--y:390;--w:210;--h:66"><b>Memory Manager</b><span>Extract candidate memories</span></div>
<div class="fig-box" style="--x:750;--y:390;--w:210;--h:66"><b>Validate, review, store</b></div>
<div class="fig-box solid" style="--x:750;--y:520;--w:210;--h:66"><b>Task Manager</b><span>Complete and archive</span></div>
<span class="fig-label muted small" style="--x:62;--y:280">No</span>
<span class="fig-label muted small" style="--x:168;--y:262">Yes</span>
</div>
</figure>

A diagnosis starts when Task Manager creates a task. LangGraph routes it to the appropriate agent graph. Before every model node, Context Manager assembles the current task context. Nodes call databases, knowledge bases, and domain tools through MCP, and their evidence enters Evidence Store.

When data is insufficient or human intervention is needed, Task Manager marks the business task WAITING and LangGraph pauses through Interrupt. When conditions are met, the task resumes, revalidates key evidence, and continues. After the report is generated, Memory Manager extracts candidate memories and stores them after validation or review. Task Manager then completes and archives the task.

LangGraph connects the flow, the three Managers handle context, memory, and task state, and the underlying services continue to provide models, data, knowledge, and algorithms.

## 08 Closing thoughts

In a local deployment, performance is not measured by token count alone. We also care about diagnostic accuracy, evidence coverage, memory-retrieval quality, retrieval latency, end-to-end response time, inference efficiency, and local compute utilization.

Agent Memory for power systems is more than adding a vector database or saving every historical conversation. It organizes current reasoning, cross-task experience, and long-running task execution into one operating mechanism.

We want power-system agents to do more than complete one diagnosis. They should accumulate experience across tasks, preserve the path back to evidence, handle interruptions, and update their judgment when operating data changes. This is the value of Agent Memory in vertical-domain engineering.
