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

"""Validate and store PC credentials through Dify's actual credential owner."""

import json

from console import Console
from prepare import STATE


def main():
    console = Console()
    provider = "oceanbase/powercontext/powercontext"
    path = f"/workspaces/current/tool-provider/builtin/{provider}"
    credentials = console.get(f"{path}/credentials")
    if not any(item["name"] == "PC Local" for item in credentials):
        console.post(
            f"{path}/add",
            json={
                "name": "PC Local",
                "type": "api-key",
                "credentials": {
                    "base_url": "http://127.0.0.1:31800",
                    "token": console.secrets["PC_TOKEN"],
                    "namespace": "pc-dify-local",
                },
            },
        )
        credentials = console.get(f"{path}/credentials")
    selected = [item for item in credentials if item["name"] == "PC Local"]
    target = STATE / "provider.json"
    target.write_text(json.dumps({"provider": provider, "credentials": selected}, indent=2) + "\n")
    target.chmod(0o600)
    print("PC provider credential is available in the local workspace.")


if __name__ == "__main__":
    main()
