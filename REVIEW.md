# Code review guidelines

Apply [AGENTS.md](AGENTS.md) and the affected contract. Focus on defects introduced by the change and decisions the author needs to make.

## Establish the behavior

- Read the issue or RFC, diff, and affected callers. Follow the public operation through its persistence or integration boundary; a changed helper alone rarely explains the outcome.
- Record the reviewed commit. Check for updates and existing feedback before publishing; recheck findings affected by new commits rather than repeating resolved issues.
- Support a finding with specific inputs or state, the expected behavior, and the actual result. Prefer a small public API reproduction. A clear code path can establish a defect; distinguish it from executed evidence and hypotheses.
- Run relevant checks. Do not claim backend or host acceptance from a mock or a directly invoked script. Passing tests do not dismiss a demonstrated failure, and missing tests alone do not establish one.

## Separate defects from design choices

Request changes for demonstrated correctness failures, such as broken authorization, invalid persisted state, lost data, or a violated API or retry contract. State the required behavior without requiring a preferred implementation.

List alternative abstractions, versioning policies, and optimization strategies as `Design note (non-blocking)` unless they violate an established requirement. Explain the tradeoff. Quantify performance concerns and distinguish extra work from failure at a supported scale or a breached budget.

Legacy input may be rejected or supported. Neither policy is inherently a defect. Accepting input that the implementation then cannot read or reuse is a defect. Check the full chosen policy, including existing records and version conflicts; do not require backward compatibility by default.

## Check corresponding implementations

- When shared runtime or contract behavior changes, check the Python client, HTTP/MCP surfaces, storage backends, and host integrations that promise that behavior. Use [integrations/capabilities.toml](integrations/capabilities.toml) to establish host support; do not assume every surface exposes every feature.
- When one public surface adds or changes an API, open its relevant counterparts and state whether an equivalent exists. If an expected equivalent is missing, identify the gap and suggest a follow-up issue.
- For a bug fix, inspect the corresponding code path before asking for the same fix elsewhere. Name the affected path and concrete failure; do not infer a shared bug from similar code.
- Respect intentional differences in backend and host support. Do not demand unsupported capabilities or identical implementations.
- Do not raise parity notes for internal refactors, tests, or documentation changes with no shared public behavior.
- Keep possible gaps brief and label them `Possible capability gap (non-blocking)`. A verified failure of an already supported contract is a correctness issue.

## Write useful comments

- Write concise English: failure, evidence, required outcome. Attach each finding to the smallest relevant changed range.
- Use one thread per issue. Link equivalent affected paths instead of repeating the same comment on each implementation.
- Put correctness findings first and label design notes separately. Avoid style preferences, generic checklists, praise, and speculative edge cases without a supported failure scenario.
- Keep test pass counts and routine validation logs out of review comments. Include a failing example or material validation limit when it explains the finding.
- Keep the review body short and use inline comments for details. Request changes when blocking findings remain; design notes alone do not justify that verdict.
- Keep feedback local unless the user asks to publish it. When authorized, submit the review and inline comments against the reviewed commit, then verify their state and locations.
