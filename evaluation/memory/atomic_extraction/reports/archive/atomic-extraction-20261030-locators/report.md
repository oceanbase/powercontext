# Atomic Memory extraction live-run report

- Status: `passed`
- Run: `atomic-extraction-20261030-locators-02`
- Dataset: `powercontext.atomic-memory-extraction.synthetic.v1` (`47e6a9e731523b6b41295cf59f6eb4772e69b1afddfa1edebfbf1fe6db021ab4`)
- Provider/model: `openai-chat` / `openai-chat:glm-5.3`
- Overall: 9/9 passed

## Quality gates

- Factual retention: 6/6 passed; false rejections: 0
- Instruction leakage: 0 leaks across 9 cases; unobserved: 0

## Prompt identities

- `coding`: `powercontext.atomic-memory.extract.coding.v2` / `87c82c353bc7a4b52a34ddcbdc1ef2cd288128b840dd5e3c6ca414efcfcf3dde`
- `conversation`: `powercontext.atomic-memory.extract.conversation.v2` / `2a603e600272542cddc7b5079853600abe977063bffe82b49822e40083e6bb38`

## Failure classes

- None

This report contains aggregate counts and run identities only. Evidence and generated candidate text remain in the ignored private run artifact.
