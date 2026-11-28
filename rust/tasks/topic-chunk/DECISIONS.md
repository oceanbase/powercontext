# Decisions

- D1 keeps Python model construction authoritative; Rust returns final codepoint ranges only.
- Use CPython 3.14.5 (managed build 20260510), Unicode16.0.0, PyO30.30.1 and maturin1.16.0.
  The S0 wheel is cp314-cp314-manylinux_2_34_x86_64. No abi3 or unsafe string-storage shortcut.
- Caches, Python installation, venvs, Cargo target and evidence are isolated under `/tmp/pc-topic-2740`.
  `UV_CACHE_DIR`, `UV_PYTHON_INSTALL_DIR`, `CARGO_HOME`, `CARGO_TARGET_DIR`, `PREK_HOME` are explicit.
  Read-only existing rustup supplies Rust1.95.0. No global tool installation is performed.
- Experimental metadata does not fetch powercontext implicitly. Install the local core wheel and native
  wheel by absolute path with `--no-deps` after installing this checkout's locked dependencies.
  Interpreter paths are explicit; run installed acceptance outside the checkout with no editable main package.
- The S0 relative-extension stub was discovered by root ty, proved by an injected assignment.
  Later absolute imports from tools did not resolve under the root project; the harness and tests
  now use package-relative imports, with a standard-library launcher and installed-package test paths.
  The root check remains strict without root edits. `ty.toml` additionally supports explicit local-project checking. Local Ruff extends root rules,
  allowing asserts only in experiment tests and reviewed subprocess use only in the harness.
- `prek -a` omits untracked files; explicit inventory checks are mandatory. An injected S101 violation
  in the untracked adapter failed the actual fixed hook, then original bytes were restored.
- The current published contract is 2026-11-22.1. Its A-03 invalid-input checks and two-round CORRECT rule
  apply. Builtin str remains the computation domain; invalid inputs must not silently disappear from reports.

- Fast-forwarded the task branch without creating a commit to verified master1840db817.
  #2735 remains open (head641ca659 at opening); task directory ownership is unchanged.
  Core chunking/models/tests were identical; rebuilt the core wheel and refreshed locked dependencies.
- Golden transport uses lossless typed tags, including surrogate UTF32LE and verified uniform-string repetition.
  It never drops text or wildcards mismatches. Target-flush and short-tail fixtures distinguish actual boundary branches.
- Actual installed core/adapter/monitor source hashes are checked at startup. Clean-install evidence rejects source shadowing
  and editable distributions; the native binary and every packaged Python file are bound to the final build.
- SQLite/API reference and candidate both stalled in the restricted sandbox. Identical real tests passed on a host
  permitting local communication; no product code or test assertion was changed to accommodate that environment.
- Use the official checksum-verified rust-anything3.1.0 executable with a test-only lossless tool_outcome boundary.
  All67 business results are ordinary strings retaining exact original return/error identity, fields and values;
  add1 separate malformed-protocol rejection with strict class/text and native_delta0. Preserve raw D1 behavior.
  The reference wrapper anchors every result to the existing golden; candidate calls remain under NativeMonitor.
  Fresh per-process receipts bind run/context/source/golden/binary and every request, with explicit completion.
  Official68-case gate and task-owned strict67-case gate both pass, with real mutation controls.
- Cargo [lib].name matches the tool's package convention; PyO3 still exports the _native Python module and the
  wheel targets cp314. Archive/remove the tool-generated _native.abi3.so before wheel building and require one
  extension in the wheel. Official direct-Cargo and maturin binaries differ; record and exercise each identity.
  The generated abi3 filename is not an ABI guarantee. Tool-wrapper bench includes test encoding/receipt costs
  and cannot replace the unwrapped D1 benchmark or authorize production adoption.
