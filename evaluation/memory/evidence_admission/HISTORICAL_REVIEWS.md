# Historical review receipts

[中文说明](HISTORICAL_REVIEWS.zh.md)

These receipts contain judgements about recorded executions, not reusable experiment settings.
They are distributed separately from implementation, fixtures and frozen protocol parameters.
The original [result archive](RESULTS_ARTIFACT.md), its bytes and checksum remain unchanged.

## Supplement availability and verification

The supplement is available in the [publication comment](https://github.com/oceanbase/powercontext/pull/2541#issuecomment-7166559603).
A downloaded copy was verified byte for byte against the original archive. Both review-receipt checksums were also verified.

- Archive: `powercontext-2074-review-receipts-0c467f5b.tar.gz`
- Download: [powercontext-2074-review-receipts-0c467f5b.tar.gz](https://github.com/user-attachments/files/35418256/powercontext-2074-review-receipts-0c467f5b.tar.gz)
- Archive size: 3,220 bytes
- SHA-256: `6237f127b295f506ee8e7bc06e0f165ac71db38874428c7c7f047e50d4af7cee`
- Source revision: `0c467f5b79fc1abec3236dd730d9e91227c217dd`
- Member root: `historical-reviews/v1/`

| Member relative to that root | Bytes | SHA-256 |
| --- | ---: | --- |
| `human-sample-review-v1.json` | 13967 | `0febe93341dd28294cca698f009891badacdd2cff9fb50be8cccfa99d44e9dbf` |
| `v6/recorded-atom-reviews.json` | 3089 | `b9c48da969c4d7e95f8da0741ea67b03cd60319f2067c05a01758ebac0a75e2f` |

`index.json` records member sizes, hashes, original paths and the original result archive checksum.
The loader uses the expected receipt checksum pinned by code or the frozen v6 contract, not an untrusted index value.
The declaration in the original result archive is not a byte-identical substitute for this source declaration.

Set `REVIEW_ARCHIVE` to the local download path and `REVIEW_EXTRACT` to an external extraction directory.
Download, verify and extract the supplement:

```bash
curl --fail --location --output "$REVIEW_ARCHIVE" \
  'https://github.com/user-attachments/files/35418256/powercontext-2074-review-receipts-0c467f5b.tar.gz'
printf '%s  %s\n' '6237f127b295f506ee8e7bc06e0f165ac71db38874428c7c7f047e50d4af7cee' "$REVIEW_ARCHIVE" | sha256sum --check -
tar -tzf "$REVIEW_ARCHIVE"
mkdir -p "$REVIEW_EXTRACT"
tar -xzf "$REVIEW_ARCHIVE" -C "$REVIEW_EXTRACT"
export POWERCONTEXT_EVIDENCE_ADMISSION_REVIEWS="$REVIEW_EXTRACT/historical-reviews/v1"
```

`POWERCONTEXT_EVIDENCE_ADMISSION_RESULTS` still points to the original extracted `results` directory.
The two variables identify separate bundles. Default offline tests need neither bundle, network access nor credentials.
Loaders do not download, repair or substitute missing receipts; missing files and byte/hash changes are explicit errors.
Bundle-relative paths and symlinks cannot escape the configured review root. Explicit custom declaration paths are
operator-selected and verified against their explicitly supplied checksum.

## Supported readers and identity

v6 historical replay verifies the original `atom_reviews_sha256` from the unchanged frozen contract before using
assistant judgements, retains the original manifest binding, and rechecks the receipt before publishing its report.
Ordinary v6 protocol loading and v7 dependency identity do not require external review files. The current v6 identity
names the external dependency and its **expected** checksum; it does not claim the bytes were read or verified.
Successful new replay reports separately record the verified receipt checksum.

The current implementation has a new code identity. Existing reports, frozen code identities and historical receipts
are never relabelled as executions of the current reader. Original historical reproduction still requires the
[matching checkout, locked environment and private audit material](HISTORY_REPRODUCTION.md).
Missing private responses remain unavailable; no new model calls are made by these readers.

The sample-review `build` command loads the pinned historical declaration from the review root when `--declaration`
is omitted. For an explicitly selected declaration, provide both `--declaration PATH` and `--declaration-sha256 SHA256`.
Byte verification precedes the existing result, queue, preparation, state, output and candidate binding checks.
A matching file checksum does not establish a matching session, complete semantic review or formal acceptance.
See [the human-review procedure](HUMAN_REVIEW.zh.md) for full commands.

The receipts preserve historical partial human judgements and assistant diagnostics. They do not rescore the archived
results, remove unresolved semantics or change the frozen `insufficient_evidence` decision.
