Some context before we start: the billing team settled the ledger API last week. Settlements are posted to
`/v3/ledger/settle`, and every call to it must carry the `X-Ledger-Idempotency-Key` header, so that a retried call
cannot settle twice. No action is needed on that yet.

For now, please fix the spelling mistake "aproved" in /workspace/README.md; it should be "approved". Do not change
anything else.
