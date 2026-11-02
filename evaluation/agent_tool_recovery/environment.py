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

"""Proxy exclusions shared by evaluation host processes and the disposable Dream Server."""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager

_proxy_environment_lock = threading.RLock()


def loopback_proxy_settings(environ: Mapping[str, str]) -> dict[str, str]:
    """Keep loopback direct and preserve the effective exclusion list used by HTTP clients."""

    # Lowercase wins on case-sensitive platforms, including an explicitly empty override.
    exclusions = environ.get("no_proxy", environ.get("NO_PROXY", ""))
    hosts = {item.strip() for item in exclusions.split(",") if item.strip()}
    value = "*" if "*" in hosts else ",".join(sorted(hosts | {"127.0.0.1", "localhost"}))
    return {"NO_PROXY": value, "no_proxy": value}


@contextmanager
def loopback_proxy_environment() -> Iterator[None]:
    """Give Dream's SDK clients and spawned Workers direct access to the local fake model.

    Keep the overrides until shutdown: Workers can start after admission. Serialize these contexts so concurrent
    Dream Servers cannot restore each other's environment, while allowing nested Servers in the same thread.
    """

    with _proxy_environment_lock:
        settings = loopback_proxy_settings(os.environ)
        original = {name: os.environ.get(name) for name in settings}
        try:
            os.environ.update(settings)
            yield
        finally:
            for name, value in original.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
