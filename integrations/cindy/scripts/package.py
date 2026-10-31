# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Package the standalone Cindy hook adapter from an explicit, credential-free file list."""

import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

root = Path(__file__).resolve().parents[3]
plugin = root / "integrations/cindy/plugins/powercontext-hooks"
manifest = json.loads((plugin / "ghost.json").read_text(encoding="utf-8"))
output = root / "dist" / f"powercontext-cindy-hooks-{manifest['version']}.cindy"
files = ("ghost.json", "main.js", "settings.html", "settings.js", "assets/icon.png", "node/worker.cjs")
for name in files:
    if not (plugin / name).is_file():
        raise FileNotFoundError(plugin / name)
output.parent.mkdir(parents=True, exist_ok=True)
with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
    for name in files:
        archive.write(plugin / name, name)
    archive.write(root / "LICENSE", "LICENSE")
print(output)
