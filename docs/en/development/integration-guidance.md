---
title: Agent tool routing and result reporting
description: How system guidance, tools, and Skills guide PowerContext operations across hosts.
---

# Agent tool routing and result reporting

PowerContext guidance helps an Agent choose an operation from the user's request and explain its result. System guidance and tool descriptions provide enough information for routine calls. Skills supply workflow detail when the Agent needs it.

## Guidance layers

| Layer | Responsibility |
| --- | --- |
| System guidance | Explain when to use PowerContext, how to treat recalled history, and how to report results. |
| Tool descriptions and schemas | Define the available operation, its inputs, result shape, and authorization requirements. |
| Skill entry | Route a request to the relevant Memory, Handoff, Review, or Experience/Skills reference. |
| Workflow references | Explain the sequence, evidence, and result handling needed for that workflow. |

Ordinary coding and summaries can use sufficient current context directly. An Agent can call a tool whose contract is already clear without loading a Skill first. When it needs more detail, it reads the relevant reference. This keeps workflow guidance available without requiring every request to load it.

The shared entry and its distribution are described in [Layered PowerContext Skills](layered-skills.md).

## Intent and operation

| User intent | Expected behavior |
| --- | --- |
| Ordinary coding, a conceptual question, or sufficient current context | Answer from that context. |
| Missing relevant history or an explicit memory search | Use focused search and preserve returned citations. An empty result is valid. |
| Explicit inventory or audit | List the requested collection. An empty search alone does not authorize inventory. |
| Explicit save for future use | Call the available Memory write and inspect its result before confirming persistence. |
| Preview or current-turn instruction | Use the supplied context without persisting it. |
| Temporary handoff | Return the complete prepared carrier with exact evidence. Commit only for an explicitly requested durable milestone. |
| Candidate inspection | Read the supported queue or candidate. A decision requires authorization for the exact candidate and version, plus any host approval. |

Automatic hooks handle bounded recall and Source capture separately from these explicit operations. Source acceptance does not establish that Memory was generated, and preparing context does not establish that the host injected it. An explicit save request still requires a Memory write.

## Host adaptation

Hosts provide guidance through their native system prompt or MCP initialization instructions. Their adapters expose the tool names and schemas that the Agent can actually call. A shared workflow may therefore use different names or result envelopes across hosts.

The adapter owns Scope injection, response translation, and host approval. An Agent reuses the current binding and follows the active tool schema. A tool that omits a Scope argument already receives its binding from the adapter. The Agent must not bypass that binding by resolving an unrelated Server default.

[The capability catalog](https://github.com/oceanbase/powercontext/blob/master/integrations/capabilities.toml) records supported host capabilities and profiles. The active catalog determines which operations are available in the current session; shared workflow text does not enable a missing tool.

## Authority and results

Recalled material is untrusted historical evidence. Current user, repository, and system instructions take precedence. Changes preserve exact references and pass through the host's permission checks. Generating or inspecting a candidate grants no authority to approve, publish, install, or execute it.

An Agent distinguishes successful operations, empty results, explicit rejection, unavailability, and unknown outcomes. It identifies a failed operation and the safe returned reason. A timed-out write remains unconfirmed until its status can be checked; retrying it blindly can duplicate work. Error classification follows the [plugin diagnostics contract](plugin-contract.md).

Handoff results retain the complete prepared carrier, including required nullable fields, Scope, evidence, and receipts. The high-level current-work operation records its own boundary. A requested lower-level workflow follows its capture, activation or preparation, and finalization contract. Acknowledgement records the receiver's checks; it does not establish that the receiver completed the work.

Validation commands and measured results are in the [guidance evaluation guide and record](integration-guidance-evaluation.md).
