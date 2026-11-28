# Build the frozen reference core

Use this procedure before the frozen diff, task gate or official tool workflow. The experiment is built from
the PR checkout; the core wheel used by both Python environments is built from the fixed reference commit.
Building the core from the PR checkout changes its VCS-derived version and fails the existing runtime check.

The source revision is `1840db817ac6e3f39ce7645c89733648e8892419`. The recorded distribution version is
`1.5.3.dev249+g1840db817`. Both remain part of this experiment's frozen identity.

## Export and build

Set `REPO` to the absolute PR checkout path and set the writable build/cache variables from README.
`PC_TOPIC_RUN_ROOT` must be outside the checkout. Start with a fresh `frozen-core-build` directory.
The local repository must contain the fixed commit; a shallow PR checkout may need its history fetched first.

```bash
set -eu
TASK="$REPO/rust/topic-chunk-rs"
CORE_REV=1840db817ac6e3f39ce7645c89733648e8892419
CORE_BUILD="$PC_TOPIC_RUN_ROOT/frozen-core-build"
mkdir "$CORE_BUILD"
CORE_SOURCE="$CORE_BUILD/source"
CORE_WHEELS="$CORE_BUILD/wheels"
mkdir "$CORE_SOURCE" "$CORE_WHEELS"
git -C "$REPO" cat-file -e "$CORE_REV^{commit}"
git -C "$REPO" archive --format=tar --output "$CORE_BUILD/source.tar" "$CORE_REV"
tar -xf "$CORE_BUILD/source.tar" -C "$CORE_SOURCE"
python3 - "$TASK" "$CORE_SOURCE" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

task, source = map(Path, sys.argv[1:])
manifest = json.loads((task / "corpus/manifest.json").read_text())
for relative, expected in manifest["reference"].items():
    if not relative.startswith("rust/"):
        if hashlib.sha256((source / relative).read_bytes()).hexdigest() != expected:
            raise SystemExit(f"Frozen core source mismatch: {relative}")
print("Frozen core source and lock fingerprints match")
PY
CORE_VERSION="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["runtime"]["powercontext"])' "$TASK/corpus/manifest.json")"
SETUPTOOLS_SCM_PRETEND_VERSION="$CORE_VERSION" \
  uv build --wheel "$CORE_SOURCE" --out-dir "$CORE_WHEELS"
CORE_WHEEL="$CORE_WHEELS/powercontext-$CORE_VERSION-py3-none-any.whl"
python3 - "$TASK" "$CORE_WHEEL" <<'PY'
import hashlib
import json
import sys
from email import message_from_bytes
from pathlib import Path
from zipfile import ZipFile

task, wheel = map(Path, sys.argv[1:])
manifest = json.loads((task / "corpus/manifest.json").read_text())
with ZipFile(wheel) as archive:
    metadata_names = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
    if len(metadata_names) != 1:
        raise SystemExit("Expected one core distribution")
    metadata = message_from_bytes(archive.read(metadata_names[0]))
    if metadata["Name"] != "powercontext" or metadata["Version"] != manifest["runtime"]["powercontext"]:
        raise SystemExit("Frozen core distribution identity mismatch")
    for relative, expected in manifest["runtime"]["installed_core_sources"].items():
        member = Path(relative).relative_to("src").as_posix()
        if hashlib.sha256(archive.read(member)).hexdigest() != expected:
            raise SystemExit(f"Frozen wheel source mismatch: {member}")
print(json.dumps({"version": metadata["Version"], "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest()}))
PY
```

The command-scoped `SETUPTOOLS_SCM_PRETEND_VERSION` supplies the recorded version to Hatch VCS because
the Git archive has no VCS metadata. The exported source is still the exact fixed commit and is checked before
building. This avoids dependence on checkout dirtiness, later tags or Git abbreviation settings. Do not apply
this override to a core built from the current PR or another revision.

The verification establishes the recorded distribution version and reference module bytes. Archive the new
wheel's actual SHA-256 with the run; identical wheel container bytes to the original build are not required.
The harness, runtime comparison, reference fingerprints, corpus manifest and golden remain unchanged.

## Install the same core in both environments

Set `REFERENCE` and `NATIVE` to absolute paths for two fresh venvs. Use the fixed CPython 3.14.5 build, export
dependencies from the frozen source, and synchronize both environments before installing the local wheels:

```bash
uv venv --python 3.14.5 "$REFERENCE"
uv venv --python 3.14.5 "$NATIVE"
uv export --project "$CORE_SOURCE" --locked --format requirements-txt \
  --no-emit-project --no-emit-local --output-file "$CORE_BUILD/requirements.txt"
uv pip sync --python "$REFERENCE/bin/python" "$CORE_BUILD/requirements.txt"
uv pip sync --python "$NATIVE/bin/python" "$CORE_BUILD/requirements.txt"
uv pip install --python "$REFERENCE/bin/python" --no-deps "$CORE_WHEEL"
uv pip install --python "$NATIVE/bin/python" --no-deps "$CORE_WHEEL" "$NATIVE_WHEEL"
uv pip check --python "$REFERENCE/bin/python"
uv pip check --python "$NATIVE/bin/python"
```

`NATIVE_WHEEL` is the exact experiment wheel built from the PR checkout using README's locked maturin
command. The reference environment must not contain that distribution. Run from outside the checkout using
absolute interpreter paths and no `PYTHONPATH`; do not install editable copies or resolve the core from PyPI.

Then run the existing README diff/gate commands and TOOL-ACCEPTANCE.md workflow. They still check the exact
Python executable, Python/Unicode/Pydantic versions, installed module bytes, local reference fingerprints and
golden hash. A different interpreter build or changed reference/lock must fail rather than rerecord the golden.
