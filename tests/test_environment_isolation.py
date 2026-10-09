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

"""Protect daily tests from inherited developer runtime configuration."""

from pathlib import Path

import pytest

pytest_plugins = ["pytester"]


@pytest.mark.parametrize("real_e2e", [False, True])
def test_runtime_environment_isolation(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, real_e2e: bool
) -> None:
    """Clear inherited settings in daily tests while preserving explicit live runs and backend opt-ins."""
    pytester.makeconftest(Path(__file__).with_name("conftest.py").read_text(encoding="utf-8"))
    inherited = {
        "POWERCONTEXT_SERVER_DATABASE_PATH": "/developer/database.sqlite",
        "POWERCONTEXT_CLIENT_API_TOKEN": "developer-token",
        "POWERCONTEXT_CODEX_CAPTURE_PROMPTS": "false",
        "powercontext_server_http_port": "8999",
    }
    for name, value in inherited.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("POWERCONTEXT_TEST_OCEANBASE_URL", "test-backend-opt-in")
    monkeypatch.setenv("NO_PROXY", "example.org")
    monkeypatch.setenv("no_proxy", "example.org")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    pytester.makepyfile(
        test_environment=f'''
        import os
        import subprocess
        import sys
        from urllib.request import proxy_bypass

        def test_environment(request, monkeypatch):
            """Verify settings isolation also applies to child processes."""
            inherited = {inherited!r}
            live = request.config.getoption("run_real_e2e")
            for name, value in inherited.items():
                assert os.environ.get(name) == (value if live else None)
            assert os.environ["POWERCONTEXT_TEST_OCEANBASE_URL"] == "test-backend-opt-in"
            assert os.environ["HTTP_PROXY"] == "http://127.0.0.1:1"
            assert proxy_bypass("example.org")
            if not live:
                assert proxy_bypass("localhost")
                assert proxy_bypass("127.0.0.1")
                assert proxy_bypass("::1")
            probe = "import os; assert ('POWERCONTEXT_SERVER_DATABASE_PATH' in os.environ) == " + repr(live)
            subprocess.run([sys.executable, "-c", probe], check=True)
            monkeypatch.setenv("POWERCONTEXT_SERVER_HTTP_PORT", "8123")
            assert os.environ["POWERCONTEXT_SERVER_HTTP_PORT"] == "8123"
        '''
    )
    arguments = ["--run-real-e2e"] if real_e2e else []
    result = pytester.runpytest_subprocess("-q", *arguments)
    result.assert_outcomes(passed=1)
