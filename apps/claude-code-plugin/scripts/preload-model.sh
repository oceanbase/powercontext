#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
# shellcheck disable=SC1091
. "$SCRIPT_DIR/common.sh"

PYTHON=${1:-}
if [ -z "$PYTHON" ]; then
  if [ -x "$(venv_python)" ]; then
    PYTHON=$(venv_python)
  else
    PYTHON=$(choose_python)
  fi
fi

echo "Preloading all-MiniLM-L6-v2 with Python: $PYTHON ($(python_version "$PYTHON"))"

"$PYTHON" -m pip install -q modelscope

"$PYTHON" - <<'PY'
import json
import os
import shutil
import urllib.request

from modelscope import snapshot_download

print("Downloading via ModelScope: AI-ModelScope/all-MiniLM-L6-v2")
snapshot_download("AI-ModelScope/all-MiniLM-L6-v2")

src = os.path.expanduser("~/.cache/modelscope/hub/models/AI-ModelScope/all-MiniLM-L6-v2")
hub = os.path.expanduser("~/.cache/huggingface/hub/models--sentence-transformers--all-MiniLM-L6-v2")

try:
    resp = urllib.request.urlopen(
        "https://huggingface.co/api/models/sentence-transformers/all-MiniLM-L6-v2",
        timeout=5,
    )
    rev = json.load(resp)["sha"]
except Exception:
    rev = "fa97f6e7cb1a59073dff9e9d8ba1c7c1591cc08d"

snap = os.path.join(hub, "snapshots", rev)
os.makedirs(snap, exist_ok=True)
os.makedirs(os.path.join(hub, "refs"), exist_ok=True)
with open(os.path.join(hub, "refs", "main"), "w") as f:
    f.write(rev)

skip = {"configuration.json", "data_config.json"}
for name in os.listdir(src):
    if name in skip:
        continue
    source = os.path.join(src, name)
    target = os.path.join(snap, name)
    if os.path.exists(target):
        continue
    if os.path.isdir(source):
        shutil.copytree(source, target)
    else:
        shutil.copy2(source, target)

print("ModelScope download and HuggingFace cache bridge complete.")
print(f"HF cache snapshot: {snap}")
PY

