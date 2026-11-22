# Official tool acceptance

The tested tool is the official rust-anything v3.1.0 Linux x86_64 precompiled executable.
Its archive SHA256 is `80d8d8bf926851ae6fd9d66b5a34393bc81bc2d10b7499aaef46a6f4c06bc48f`;
its executable SHA256 is `a369ae1aca27155a30cccfebdeb5e8029c03fa1af1ef56dbb2c7d7a7649586b5`.
Both the release asset digest and SHA256SUMS were checked. No global setup or toolchain upgrade is needed.
Source analysis is pinned to release commit `8ac2c1c8ab77bd1a20588f0c03f0a72ab99a046e` in
[erkamyaman/rust-anything](https://github.com/erkamyaman/rust-anything/releases/tag/v3.1.0).

## Verified boundary

The official Python codec cannot preserve this task's raw surrogate inputs, and its exception comparator accepts
some wrong exception types/messages. The test-only `tool_outcome` boundary avoids both limitations:

1. Versioned ASCII requests retain exact Python types, missing arguments and UTF-32LE surrogate codepoints.
2. The reference process calls the original installed core and compares every captured outcome with the frozen
   golden before emitting a result. It contains no candidate package during recording.
3. The candidate calls the existing D1 adapter under the existing mandatory-native monitor.
4. Return types, model identities, fields, exception qualified types/text/args and Pydantic errors are losslessly
   encoded as an ordinary string. Official diff compares that string exactly; it does not compare business errors
   through its permissive exception path. Codec, identity and monitor failures remain failures.

The tool input contains67 business calls and1 additional malformed-protocol rejection. These are separate
denominators: business N=R=63, F=E=0, with4 invalid/missing calls separate; the protocol rejection is exactly
`builtins.ValueError: Invalid tool request` with zero native increment. The original67-case corpus is unchanged.
Requests round-trip before recording, including the distinction between an explicit surrogate pair and one scalar.

Official build, record, diff, bench and gate ran successfully against the actual source crate. Diff and gate
each matched68/68. Each process has a separate run ID and receipt binding context/source/golden identity,
request order/hashes, actual binary, counters and the terminal protocol rejection. `tool_check.py` rejects partial
or stale receipts. Official gate alone is not a substitute for that native-path receipt.

Six disposable-copy negative controls passed: wrong business exception type, text and Pydantic details each
produce one targeted mismatch; wrong ordinal produces field mismatches with normal native completions;
a swallowed reference trap fails sticky/profile checks; a missing extension fails import. Restored replay passes.
The official tool's static error-path check establishes that the wrapper has a recorded rejection, not that every
environment/context/golden guard was dynamically covered. The genuine malformed-protocol case is tested strictly
and is not used to claim complete coverage of those guards.

## Reproduce

Use absolute `TASK`, `TOOL`, `REFERENCE` and `NATIVE` paths. Both Python environments must be the fixed
CPython3.14.5; dependencies/core come from this checkout's locked resolution and explicit local wheel.
The reference environment must not contain the candidate. Set the writable cache/build variables from README.
`RUN` must be a fresh directory outside the checkout. The following commands do not rerecord the frozen golden.

```bash
"$REFERENCE/bin/python" -B -I "$TASK/tools/tool_prepare.py" --output "$RUN"
export PC_TOPIC_TOOL_CONTEXT="$RUN/context.json"
export PC_TOPIC_TOOL_CONTEXT_SHA256="$("$REFERENCE/bin/python" -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$PC_TOPIC_TOOL_CONTEXT")"
PC_TOPIC_TOOL_PHASE=record PATH="$REFERENCE/bin:$PATH" \
  "$TOOL" record "$TASK" --count 0 --inputs "$RUN/inputs.jsonl"
PYO3_PYTHON="$NATIVE/bin/python" PATH="$NATIVE/bin:$PATH" "$TOOL" build "$TASK"
export PC_TOPIC_TOOL_PHASE=candidate
export PC_TOPIC_TOOL_STATS="$RUN/receipts"
export PC_TOPIC_TOOL_BINARY_SHA256="$("$NATIVE/bin/python" -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$TASK/python/powercontext_topic_chunk_rs/_native.abi3.so")"
for check in diff gate; do
  export PC_TOPIC_TOOL_RUN_ID="$("$NATIVE/bin/python" -c 'import uuid; print(uuid.uuid4().hex)')"
  PATH="$NATIVE/bin:$PATH" PYO3_PYTHON="$NATIVE/bin/python" "$TOOL" "$check" "$TASK" --json || exit
  "$NATIVE/bin/python" -B -I "$TASK/tools/tool_check.py" \
    --context "$PC_TOPIC_TOOL_CONTEXT" --run-id "$PC_TOPIC_TOOL_RUN_ID" \
    --stats "$PC_TOPIC_TOOL_STATS" --binary "$TASK/python/powercontext_topic_chunk_rs/_native.abi3.so" || exit
done
export PC_TOPIC_TOOL_PHASE=bench
export PC_TOPIC_TOOL_RUN_ID="$("$NATIVE/bin/python" -c 'import uuid; print(uuid.uuid4().hex)')"
PATH="$NATIVE/bin:$PATH" "$TOOL" bench "$TASK"
```

`tool_check.py` verifies one complete candidate replay, not repeated benchmark cycles. Benchmark receipts may
contain multiple complete cycles and do not use its single-replay completion claim. Official wrapper bench
includes serialization, frozen-golden checks and receipt I/O; it is a protocol measurement, not D1 performance.
Use the existing unwrapped benchmark for product-cost analysis. Python remains the default.

## Build and installation identity

The official direct Cargo build and maturin wheel have distinct binary identities. Both are exercised:

- Direct tool extension SHA256: `16d56db9fc5e945eeeb08a398942d22014163332bf70934b7924dc1914acfded`.
- Wheel and installed extension SHA256: `943bd56d65712766ca3eaee835588d8c35d2a3acfb54b22f23291cf3b38d986b`.

Official68-case diff also passes against a package assembled from the exact wheel bytes. The clean installed
package separately replays the same68 requests with a valid native receipt, passes the original67 strict cases,
25 experimental tests and104 original focused/SQLite/API tests. No editable install is used.

The tool writes `_native.abi3.so` into the source Python package. This filename does not establish ABI3 support.
Before wheel building, archive and remove that generated file so it cannot be bundled beside the intended cp314
extension. The tested wheel contains exactly one extension; its bytes equal the installed extension. Do not
silently treat an official rebuild and a wheel as the same binary. Keep independent locked Cargo/build checks:
not all internal official-tool Cargo commands use --locked.

Generated tool cases, recorded-from metadata, driver and source extension are ignored local artifacts, not
replacements for the checked-in corpus. Freeze their actual bytes as explicit snapshot inputs, along with
context, requests, tool binary, receipts, wheels and raw logs. Archive them before cleaning temporary directories.
Current raw evidence is under `/tmp/pc-topic-2740/tool-adapter/`; machine summary is `evidence/tool-acceptance.json`.

This is local Linux/cp314 experiment acceptance. Maintained native CI, other platforms, package publication,
production adoption and Issue closure remain separate decisions.
