# Atomic Memory extraction live-run report

- Status: `passed`
- Run: `atomic-extraction-20261030-merged-locators-120s`
- Dataset: `powercontext.atomic-memory-extraction.synthetic.v1` (`47e6a9e731523b6b41295cf59f6eb4772e69b1afddfa1edebfbf1fe6db021ab4`)
- Provider/model: `openai-chat` / `openai-chat:glm-5.3`
- Overall: 9/9 passed

## Quality gates

- Factual retention: 6/6 passed; false rejections: 0
- Instruction leakage: 0 leaks across 9 cases; unobserved: 0

## Prompt identities

- `coding`: `powercontext.atomic-memory.extract.coding.v3` / `49f89a1a61e5f551780f934bf447b353a199545fa547ac6aee0a1e8c0e34aee8`
- `conversation`: `powercontext.atomic-memory.extract.conversation.v3` / `44c6c4969c7c4b984d845af1c4e94270d556443e133e862650ad226a7f508475`

## Failure classes

- None

This report contains aggregate counts and run identities only. Evidence and generated candidate text remain in the ignored private run artifact.
