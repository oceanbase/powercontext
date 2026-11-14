/*
 * Copyright (c) 2026 OceanBase.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

// generated from openapi/powercontext.yaml; do not edit.

export const HANDOFF_HTTP_SCHEMAS = {
  ArtifactReference: {"type":"object","additionalProperties":false,"required":["family","artifact_id","revision"],"properties":{"family":{"type":"string","minLength":1,"maxLength":128,"pattern":"^[\\x21-\\x7E]+$"},"artifact_id":{"type":"string","minLength":1,"maxLength":128,"pattern":"^[\\x21-\\x7E]+$"},"revision":{"type":"integer","minimum":1}}},
  CaptureContentSourceResponse: {"type":"object","additionalProperties":false,"required":["status","source","position"],"properties":{"status":{"$ref":"CaptureStatus"},"source":{"$ref":"SourceReference"},"position":{"type":"integer","minimum":1}}},
  CaptureStatus: {"type":"string","enum":["accepted"]},
  CurrentWorkHandoff: {"type":"object","additionalProperties":false,"required":["schema","trust","objective","state","disposition","next_action","omissions"],"properties":{"schema":{"type":"string","enum":["powercontext.current-work-handoff.v1"]},"trust":{"type":"string","enum":["untrusted_input"]},"objective":{"type":"string","minLength":1,"maxLength":8192,"pattern":".*\\S.*"},"state":{"type":"array","minItems":1,"maxItems":64,"items":{"$ref":"WorkClaim"}},"disposition":{"$ref":"HandoffDisposition"},"next_action":{"$ref":"WorkClaim","nullable":true},"omissions":{"type":"array","maxItems":64,"items":{"type":"string","minLength":1,"maxLength":8192,"pattern":".*\\S.*"}}}},
  HandoffArtifactCitation: {"type":"object","additionalProperties":false,"required":["kind","artifact_ref"],"properties":{"kind":{"type":"string","enum":["artifact"]},"artifact_ref":{"$ref":"ArtifactReference"}}},
  HandoffCitation: {"oneOf":[{"$ref":"HandoffSourceCitation"},{"$ref":"HandoffArtifactCitation"}]},
  HandoffContent: {"type":"object","additionalProperties":false,"required":["schema","objective","state","disposition","next_action","omissions"],"properties":{"generation":{"$ref":"HandoffGenerationMetadata","nullable":true},"schema":{"$ref":"HandoffSchema"},"objective":{"type":"string","minLength":1,"maxLength":8192,"pattern":".*\\S.*"},"state":{"type":"array","minItems":1,"maxItems":64,"items":{"$ref":"HandoffStatement"}},"disposition":{"$ref":"HandoffDisposition"},"next_action":{"$ref":"HandoffStatement","nullable":true},"omissions":{"type":"array","maxItems":64,"items":{"$ref":"HandoffOmission"}}}},
  HandoffDisposition: {"type":"string","enum":["continuable","blocked","complete"]},
  HandoffDraft: {"type":"object","additionalProperties":false,"required":["objective","state","disposition","next_action","omissions"],"properties":{"generation":{"$ref":"HandoffGenerationEnvelope","nullable":true},"objective":{"type":"string","minLength":1,"maxLength":8192,"pattern":".*\\S.*"},"state":{"type":"array","minItems":1,"maxItems":64,"items":{"$ref":"HandoffStatement"}},"disposition":{"$ref":"HandoffDisposition"},"next_action":{"$ref":"HandoffStatement","nullable":true},"omissions":{"type":"array","maxItems":64,"items":{"$ref":"HandoffOmission"}}}},
  HandoffGenerationEnvelope: {"type":"object","additionalProperties":false,"required":["receipt"],"properties":{"receipt":{"type":"string","minLength":1,"maxLength":8192}}},
  HandoffGenerationMetadata: {"type":"object","additionalProperties":false,"required":["scope_id","prompt_key","selection","artifact","definition_version","builtin_version","compiled_digest","original_draft_digest","edit_status"],"properties":{"scope_id":{"type":"string","minLength":1,"maxLength":256},"prompt_key":{"$ref":"HandoffPromptKey"},"selection":{"type":"string","enum":["built_in","artifact"]},"artifact":{"$ref":"ArtifactReference","nullable":true},"definition_version":{"type":"string","minLength":1,"maxLength":256},"builtin_version":{"type":"string","minLength":1,"maxLength":256},"compiled_digest":{"type":"string","pattern":"^[0-9a-f]{64}$"},"original_draft_digest":{"type":"string","pattern":"^[0-9a-f]{64}$"},"edit_status":{"type":"string","enum":["unchanged","edited"]}}},
  HandoffOmission: {"type":"object","additionalProperties":false,"required":["text","citation"],"properties":{"text":{"type":"string","minLength":1,"maxLength":8192,"pattern":".*\\S.*"},"citation":{"$ref":"HandoffCitation","nullable":true}}},
  HandoffPromptKey: {"type":"string","enum":["handoff.generate"]},
  HandoffSchema: {"type":"string","enum":["powercontext.handoff.v1"]},
  HandoffSourceCitation: {"type":"object","additionalProperties":false,"required":["kind","source_ref"],"properties":{"kind":{"type":"string","enum":["source"]},"source_ref":{"$ref":"SourceReference"}}},
  HandoffStatement: {"type":"object","additionalProperties":false,"required":["text","citations"],"properties":{"text":{"type":"string","minLength":1,"maxLength":8192,"pattern":".*\\S.*"},"citations":{"type":"array","minItems":1,"maxItems":32,"items":{"$ref":"HandoffCitation"}}}},
  PreparedHandoff: {"type":"object","additionalProperties":false,"required":["schema","scope_id","base","content"],"properties":{"generation":{"$ref":"HandoffGenerationEnvelope","nullable":true},"schema":{"$ref":"PreparedHandoffSchema"},"scope_id":{"type":"string"},"base":{"$ref":"ArtifactReference","nullable":true},"content":{"$ref":"HandoffContent"}}},
  PreparedHandoffSchema: {"type":"string","enum":["powercontext.prepared-handoff.v1"]},
  PreparedWorkHandoff: {"type":"object","additionalProperties":false,"required":["boundary","handoff"],"properties":{"boundary":{"$ref":"WorkSourceReceipt"},"handoff":{"$ref":"PreparedHandoff"}}},
  SourceLineRangeLocator: {"type":"object","additionalProperties":false,"required":["kind","start_line","end_line"],"x-powercontext-source-line-range":true,"properties":{"kind":{"type":"string","enum":["line_range"]},"start_line":{"type":"integer","minimum":1},"end_line":{"type":"integer","minimum":1},"heading_path":{"type":"string","nullable":true,"maxLength":1024}}},
  SourceLocator: {"oneOf":[{"$ref":"SourceSequenceLocator"},{"$ref":"SourceLineRangeLocator"}]},
  SourceReference: {"type":"object","additionalProperties":false,"required":["name","source_id"],"properties":{"name":{"type":"string"},"source_id":{"type":"string"},"locator":{"$ref":"SourceLocator","nullable":true}}},
  SourceSequenceLocator: {"type":"object","additionalProperties":false,"required":["kind","sequence"],"properties":{"kind":{"type":"string","enum":["sequence"]},"sequence":{"type":"integer","minimum":0}}},
  WorkClaim: {"type":"object","additionalProperties":false,"required":["text","basis","evidence"],"properties":{"text":{"type":"string","minLength":1,"maxLength":8192,"pattern":".*\\S.*"},"basis":{"$ref":"WorkClaimBasis"},"evidence":{"type":"array","maxItems":31,"items":{"$ref":"HandoffCitation"}}}},
  WorkClaimBasis: {"type":"string","enum":["declared","verified"]},
  WorkSourceKind: {"type":"string","enum":["work-contract","handoff-boundary","handoff-receipt","task-outcome"]},
  WorkSourceReceipt: {"type":"object","additionalProperties":false,"required":["kind","source","position","content_digest"],"properties":{"kind":{"$ref":"WorkSourceKind"},"source":{"$ref":"SourceReference"},"position":{"type":"integer","minimum":1},"content_digest":{"type":"string","minLength":71,"maxLength":71,"pattern":"^sha256:[0-9a-f]{64}$"}}},
} as const
