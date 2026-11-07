# Atomic Memory extraction live-run report

- Status: `passed`
- Run: `atomic-extraction-20261101-review-2359-v2-glm`
- Dataset: `powercontext.atomic-memory-extraction.synthetic.v2` (`853d22abc12ede5ccdd46e380cb935b9746c8003fc484d0934366bb8f5df22c6`)
- Provider/model: `openai-chat` / `openai-chat:glm-5.3`
- Overall: 9/9 passed

## Quality gates

- Factual retention: 6/6 passed; false rejections: 0
- Instruction leakage: 0 leaks across 9 cases; unobserved: 0

## Prompt identities

- `coding`: `powercontext.atomic-memory.extract.coding.v4` / `da5de7be4ac0e23f5e718076dc51b5f1825f376bfe2f071278ff5be2dc096a97`
- `conversation`: `powercontext.atomic-memory.extract.conversation.v4` / `503f212db750b5d0db4b5e84d6c88ad42f90e8c51bbd0af3d1a9a50e7bb6afc4`

## Failure classes

- None

This report contains aggregate counts and run identities only. Evidence and generated candidate text remain in the ignored private run artifact.
