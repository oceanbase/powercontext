# Review and publication

## Experiences, Skills, and review

Proposals and generated artifacts must include exact source or artifact
references. Read an Experience or Skill by its exact artifact reference.
Generation and import are durable operations and require user authorization.

Artifact Candidates are not active artifacts until reviewed. List or read a
candidate first; approve, reject, or revise it only when the user explicitly
requests that decision. External Skills must be scanned and resolved by
fingerprint before import.

## Human commands

Use /pc for operational actions and review decisions:

- /pc trace ... inspects evaluation traces.
- /pc scope ... manages the durable workspace Scope binding in PowerContext.
- /pc review ... lists, reads, approves, rejects, or revises candidates.
- /pc call OPERATION PAYLOAD_JSON is available for an operation not covered
  by a short command.

Candidate approval/rejection/revision are human `/pc review` commands, not provider tools. Do not invent provider
operation names or use `/pc call` to bypass that boundary. Present suggested changes for the authorized human decision.
