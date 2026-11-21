# External evaluation results

Generated reports, run records, and historical result bundles are published separately from the code pull request.
The archive preserves the former `results/` directory layout so historical hash checks and replay commands can use it
without rewriting any receipt.

- Code PR snapshot used to assemble the archive: `8bf9c58d428995c97858b6764d4563c3cfe9ea44`
- Archive: `powercontext-2074-evidence-8bf9c58d.tar.gz`
- SHA-256: `ff529bf0875c2441835bc5f35af3fe60df0c62c8722370f410aec73e499c0d65`
- Download: [powercontext-2074-evidence-8bf9c58d.tar.gz](https://github.com/user-attachments/files/34846203/powercontext-2074-evidence-8bf9c58d.tar.gz)
- Final live continuation execution revision: `1515a0d11cc2040d0beb341f6626a33c09cce73b`

The packaging snapshot is not the execution revision. Earlier runs have their own revisions and hashes inside the
archive. See [historical code and reproduction](HISTORY_REPRODUCTION.md) for the local history identity and its remote
publication status. The evidence archive contains results, not a complete Git repository or the private audit bodies.

After verifying and extracting the archive, point historical replay commands at its `results` directory:

```bash
printf '%s  %s\n' \
  ff529bf0875c2441835bc5f35af3fe60df0c62c8722370f410aec73e499c0d65 \
  powercontext-2074-evidence-8bf9c58d.tar.gz | sha256sum --check
tar -xzf powercontext-2074-evidence-8bf9c58d.tar.gz
export POWERCONTEXT_EVIDENCE_ADMISSION_RESULTS="$PWD/results"
```

## Evidence and review entry points

Every `results/...` path below is inside the downloaded archive, relative to its extraction directory. None is an
in-repository result link. The archive name, SHA-256, packaging snapshot, and final continuation execution revision
above apply to this index; individual receipts retain their own execution identities.

| Archive member | Purpose |
| --- | --- |
| `results/v20-live/` | Frozen settings, results, diagnostics, recorded replay, validation, and artifact hashes |
| `results/v20-human-sample/` | Declared sample coverage, partial judgments, byte checks, and context binding |
| `results/acceptance-closeout-20261107/validation.json` | Historical engineering validation, including failed and interrupted attempts |
| `results/acceptance-closeout-20261107/final-hooks.json` | Historical per-hook outcomes |
| `results/acceptance-closeout-20261107/artifact-index.json` | Closeout file hashes |
| `results/acceptance-closeout-20261107/COMPLETE.json` | Closeout completion marker; the recorded status remains `closed_with_failures` |

The in-repository [human review](HUMAN_REVIEW.zh.md), [acceptance mapping](ACCEPTANCE_CLOSEOUT.zh.md), and
[review confirmation](REVIEW_CONFIRMATION.zh.md) explain these records. Setting the environment variable only locates
the extracted bundle; it does not bypass identity checks, supply private outputs, or make an old run compatible with
the current runtime. Full historical replay also requires the matching code, locked environment, and private audit
material. The two unavailable historical responses remain unavailable.

Default validation does not download this archive and does not require provider credentials. Tests that exercise a
historical result bundle skip when `POWERCONTEXT_EVIDENCE_ADMISSION_RESULTS` is unset; implementation, fixtures,
manifests, current-runtime regressions, and model-free protocol tests remain self-contained in the code PR.

Historical documents retain `results/...` names as archive-relative evidence identities. Those paths resolve after
extracting the archive; they are not files in the code pull request.
