# External evaluation results

Generated reports, run records, and historical result bundles are published separately from the code pull request.
The archive preserves the former `results/` directory layout so historical hash checks and replay commands can use it
without rewriting any receipt.

- Code PR snapshot used to assemble the archive: `8bf9c58d428995c97858b6764d4563c3cfe9ea44`
- Archive: `powercontext-2074-evidence-8bf9c58d.tar.gz`
- SHA-256: `ff529bf0875c2441835bc5f35af3fe60df0c62c8722370f410aec73e499c0d65`
- Download URL: `TO_BE_ADDED_AFTER_ARTIFACT_UPLOAD`

After verifying and extracting the archive, point historical replay commands at its `results` directory:

```bash
sha256sum powercontext-2074-evidence-8bf9c58d.tar.gz
tar -xzf powercontext-2074-evidence-8bf9c58d.tar.gz
export POWERCONTEXT_EVIDENCE_ADMISSION_RESULTS="$PWD/results"
```

Default validation does not download this archive and does not require provider credentials. Tests that exercise a
historical result bundle skip when `POWERCONTEXT_EVIDENCE_ADMISSION_RESULTS` is unset; implementation, fixtures,
manifests, current-runtime regressions, and model-free protocol tests remain self-contained in the code PR.

Historical documents retain `results/...` names as archive-relative evidence identities. Those paths resolve after
extracting the archive; they are not files in the code pull request.
