# Historical code and reproduction

## Identities and availability

| Identity | Value |
| --- | --- |
| Historical repository | `Lkx-JY/powercontext` |
| Published historical branch | `evaluation/2074-bounded-evidence-admission-37commits` |
| Fixed archive tip | `3cc97c26e6cc6a6d99c678bc4da10b54755a9960` |
| Final live continuation execution revision | `1515a0d11cc2040d0beb341f6626a33c09cce73b` |
| Results packaging snapshot | `8bf9c58d428995c97858b6764d4563c3cfe9ea44` |
| Remote archive verification | Published on `Lkx-JY/powercontext`; remote SHA and a fresh fetch of the execution revision verified on 2026-11-16 |

The [fixed archive tip](https://github.com/Lkx-JY/powercontext/tree/3cc97c26e6cc6a6d99c678bc4da10b54755a9960) contains the
[execution revision](https://github.com/Lkx-JY/powercontext/tree/1515a0d11cc2040d0beb341f6626a33c09cce73b).
The PR branch was rebuilt and does not retain that execution revision in its ancestry; it is not a substitute for
this separate historical ref. The result archive is [publicly downloadable](RESULTS_ARTIFACT.md); it does not contain
the historical Git objects or the private audit bodies.

## Local code recovery

In the existing repository containing the historical objects, verify the identities before checking out a separate
worktree. These commands do not send model requests or modify the historical branch:

```bash
git rev-parse evaluation/2074-bounded-evidence-admission-37commits
git merge-base --is-ancestor \
  1515a0d11cc2040d0beb341f6626a33c09cce73b \
  evaluation/2074-bounded-evidence-admission-37commits
git worktree add --detach ../powercontext-2074-historical \
  1515a0d11cc2040d0beb341f6626a33c09cce73b
```

The first command must print the archive tip above; the second must exit successfully. Use the historical checkout's
own lock files. Current code and schema must not be used to rewrite an old run's identity or repair its outputs.

Current historical helpers locate an extracted bundle with `POWERCONTEXT_EVIDENCE_ADMISSION_RESULTS`. The historical
checkout predates that option: follow that revision's own commands and directory layout. Keep extracted results and
private audit inputs outside the code diff. Without the original private outputs, public receipt inspection and hash
checks are possible, but full response replay is not established. No replacement request recovers an unavailable
historical response.

## External review receipts

The current code tree keeps run-specific review judgements in the separate [review supplement](HISTORICAL_REVIEWS.md),
not in reusable manifests or package sources. v6 replay loads its original assistant receipt through
`POWERCONTEXT_EVIDENCE_ADMISSION_REVIEWS` and checks the unchanged contract's `atom_reviews_sha256`.
Human sample declarations are pinned by byte checksum before their original result/queue/state bindings are checked.
The [published supplement](https://github.com/user-attachments/files/35418256/powercontext-2074-review-receipts-0c467f5b.tar.gz)
is available from the [publication comment](https://github.com/oceanbase/powercontext/pull/2541#issuecomment-7166559603).
Follow the [download, checksum and extraction instructions](HISTORICAL_REVIEWS.md) before using the receipts.

Current v6/v7 dependency identities describe the implementation and the expected external receipt checksum.
They do not claim receipt verification until a replay actually reads it, and they do not match the original code
identity. Keep original reports unchanged. The historical checkout still uses its original source layout and does
not automatically adopt the current environment variable or reader. No new historical replay or provider acceptance
is established by moving these receipts or passing synthetic regressions.

## Fetch the published history

The archive branch is separate from the PR head and must be retained independently of PR merge or head-branch cleanup.
To inspect its identity and obtain the original execution code from a fresh checkout:

```bash
git ls-remote --heads https://github.com/Lkx-JY/powercontext.git \
  refs/heads/evaluation/2074-bounded-evidence-admission-37commits
git init powercontext-2074-historical
git -C powercontext-2074-historical fetch --no-tags https://github.com/Lkx-JY/powercontext.git \
  refs/heads/evaluation/2074-bounded-evidence-admission-37commits
git -C powercontext-2074-historical checkout --detach 1515a0d11cc2040d0beb341f6626a33c09cce73b
```

The remote result must identify the exact archive tip above. Use the execution revision's own lock files and commands.
The published ref establishes code availability, not availability of private audit bodies, provider credentials, or the
two missing historical responses. Rebuilding dependencies and reissuing model requests is not a replay of old output.

## Gateway scope

The refusal-coverage implementation is preserved at commit
`f348f71fb464afb61dddf0f7152f3222a91f26db`. It is excluded from the #2074 evaluation diff. A separate Gateway change
must define Chat `message.refusal` and Responses refusal-part coverage in both capture-coverage documents and verify
the status/body behavior. This document neither creates that PR nor claims the upstream refusal gap is fixed.
