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

"""Command-line entry point for application-owned coding subscription logins."""

from __future__ import annotations

import argparse
import asyncio


def main() -> None:
    parser = argparse.ArgumentParser(description="Authorize a PowerContext-owned coding subscription")
    parser.add_argument("subscription", choices=("qwen", "kimi"))
    arguments = parser.parse_args()
    if arguments.subscription == "qwen":
        from powercontext.builtin.inference.qwen import login_qwen

        login = login_qwen
    else:
        from powercontext.builtin.inference.kimi import login_kimi

        login = login_kimi
    asyncio.run(login(lambda uri, code: print(f"{uri}\nCode: {code}")))


if __name__ == "__main__":
    main()
