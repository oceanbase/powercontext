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

"""Run the real Receiver once; configuration arrives on stdin, never in argv."""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import asdict

from powercontext.client import RemoteSkillReceiver, RemoteSkillReceiverConfig


async def main() -> None:
    config = RemoteSkillReceiverConfig.model_validate(json.load(sys.stdin))
    async with RemoteSkillReceiver(config) as receiver:
        result = await receiver.sync()
    print(json.dumps(asdict(result)))


if __name__ == "__main__":
    asyncio.run(main())
