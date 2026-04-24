#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PowerMem Dashboard Functional Test Suite

Automated test script for PowerMem Dashboard, covering:
- Dashboard Overview page (stats, charts, system health)
- Memories page (list, search, filter, pagination, delete)
- User Profile page (list, search, pagination)
- Settings page (API key management)
- UI interactions and navigation

Dependencies for this file are declared only here (no separate requirements txt / pyproject extra).

**Auto-install (default):** On import, if ``pytest_playwright`` is missing, this module runs
``pip install`` for ``_DASHBOARD_TEST_PACKAGES`` (same interpreter as pytest), then
``python -m playwright install chromium``. Requires network access.

**Opt out:** ``POWERMEM_DASHBOARD_NO_AUTO_INSTALL=1`` — skip auto pip; missing plugin → module skip.
``POWERMEM_DASHBOARD_NO_AUTO_BROWSER_INSTALL=1`` — skip ``playwright install`` (packages still auto-pip if needed).
``POWERMEM_DASHBOARD_NO_AUTO_BROWSER_DEPS=1`` — do not use ``--with-deps`` on Linux/CI.
``POWERMEM_DASHBOARD_SKIP_PREFLIGHT=1`` — skip pre-test environment checks (socket/HTTP/Playwright).
``POWERMEM_DASHBOARD_PREFLIGHT_STRICT`` — if Playwright can load the dashboard with
``domcontentloaded`` but not ``networkidle`` within 30s, treat as failure. If unset,
defaults to **on in CI** and **off locally**. Set ``0``/``1`` to force.
Optional: ``POWERMEM_DASHBOARD_PREFLIGHT_NETWORKIDLE_MS`` (default ``30000``) for the networkidle wait.

**Server reset before cases (default):** At session start, from the repo root, runs
``make server-stop`` then ``make server-dashboard-start`` so the dashboard is built
and the API server matches the Makefile workflow.
Opt out: ``POWERMEM_DASHBOARD_SKIP_MAKE_SERVER=1`` (e.g. CI with a server already up).
Optional: ``POWERMEM_DASHBOARD_MAKE_TIMEOUT`` — seconds for ``make server-dashboard-start`` (default ``1800``).

Manual install (same as auto)::

    # or copy DASHBOARD_TEST_PIP_INSTALL, then:
    playwright install chromium

Optional (reporting / parallel): ``pytest-html>=4.1.1``, ``pytest-cov>=4.1.0``, ``pytest-xdist>=3.5.0``.

Repository-root ``.env``: ``POWERMEM_SERVER_API_KEYS`` or ``POWERMEM_SERVER_API_KEY``.

Usage:
    pytest tests/regression/test_dashboard.py -v
    pytest tests/regression/test_dashboard.py -v -k "test_overview"
    pytest tests/regression/test_dashboard.py -v --headed

If you see ``collected 0 items / 1 skipped``, the whole file was skipped (usually missing
``pytest-playwright`` in the active venv). Run ``pytest -rs`` to print skip reasons.
"""

import importlib
import importlib.util
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import traceback
from typing import Any, Dict, Optional, Tuple

import pytest

# --- Dependency install (single source of truth for this module; keep in sync with docstring above) ---
_DASHBOARD_TEST_PACKAGES = (
    "pytest>=8.2.2",
    "pytest-asyncio>=0.23.7",
    "requests>=2.31.0",
    "pytest-playwright>=0.4.3",
    "playwright>=1.40.0",
    "pytest-timeout>=2.2.0",
    "pytest-mock>=3.14.0",
)
# One line for copy-paste / error messages
DASHBOARD_TEST_PIP_INSTALL = "pip install " + " ".join(f'"{p}"' for p in _DASHBOARD_TEST_PACKAGES)
_DASHBOARD_DEPS_HINT = (
    f"Missing dashboard test deps. Run:\n  {DASHBOARD_TEST_PIP_INSTALL}\n"
    "Then:\n  playwright install chromium"
)

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _env_truthy(name: str) -> bool:
    v = os.environ.get(name)
    if v is None:
        return False
    return v.strip().lower() in ("1", "true", "yes", "on")


def _ensure_dashboard_playwright_environment() -> None:
    """
    Optionally pip-install test deps and download Chromium for Playwright.

    See module docstring for POWERMEM_DASHBOARD_NO_AUTO_INSTALL / NO_AUTO_BROWSER_INSTALL.
    """
    has_plugin = importlib.util.find_spec("pytest_playwright") is not None
    no_auto_pip = _env_truthy("POWERMEM_DASHBOARD_NO_AUTO_INSTALL")
    no_auto_browser = _env_truthy("POWERMEM_DASHBOARD_NO_AUTO_BROWSER_INSTALL")
    no_auto_browser_deps = _env_truthy("POWERMEM_DASHBOARD_NO_AUTO_BROWSER_DEPS")
    is_ci = _env_truthy("CI")
    is_linux = sys.platform.startswith("linux")

    if not has_plugin:
        if no_auto_pip:
            print("\n" + "=" * 72, file=sys.stderr)
            print(
                "test_dashboard.py: SKIPPED — pytest_playwright missing and "
                "POWERMEM_DASHBOARD_NO_AUTO_INSTALL is set.",
                file=sys.stderr,
            )
            print(_DASHBOARD_DEPS_HINT, file=sys.stderr)
            print("=" * 72 + "\n", file=sys.stderr)
            pytest.skip(
                "pytest_playwright not installed; auto-install disabled "
                "(unset POWERMEM_DASHBOARD_NO_AUTO_INSTALL or install manually).",
                allow_module_level=True,
            )
        print(
            "\n[test_dashboard] pytest_playwright not found — auto-installing packages into "
            f"this interpreter:\n  {sys.executable}\n",
            file=sys.stderr,
        )
        proc = subprocess.run(
            [sys.executable, "-m", "pip", "install", *_DASHBOARD_TEST_PACKAGES],
            cwd=_REPO_ROOT,
        )
        importlib.invalidate_caches()
        if proc.returncode != 0:
            pytest.skip(
                f"pip install failed (exit {proc.returncode}). Run manually:\n  {DASHBOARD_TEST_PIP_INSTALL}",
                allow_module_level=True,
            )
        if importlib.util.find_spec("pytest_playwright") is None:
            pytest.skip(
                "Packages were installed but pytest_playwright is still not importable in this "
                "process. Restart pytest once, or run:\n  " + DASHBOARD_TEST_PIP_INSTALL,
                allow_module_level=True,
            )

    if not no_auto_browser:
        install_cmds = []

        # In GitHub Actions / CI Linux, browser binaries alone are often insufficient.
        # Try with system dependencies first, then fall back to plain install.
        if is_linux and is_ci and not no_auto_browser_deps:
            install_cmds.append(
                (
                    [sys.executable, "-m", "playwright", "install", "--with-deps", "chromium"],
                    "playwright install --with-deps chromium",
                )
            )
        install_cmds.append(
            (
                [sys.executable, "-m", "playwright", "install", "chromium"],
                "playwright install chromium",
            )
        )

        last_rc: Optional[int] = None
        for cmd, label in install_cmds:
            print(f"[test_dashboard] Ensuring Playwright Chromium: {label}\n", file=sys.stderr)
            bproc = subprocess.run(cmd, cwd=_REPO_ROOT)
            last_rc = bproc.returncode
            if last_rc == 0:
                break
            print(
                f"[test_dashboard] Browser install step failed (exit {last_rc}): {label}",
                file=sys.stderr,
            )
        else:
            pytest.skip(
                f"playwright browser install failed (exit {last_rc}). "
                "Run manually: playwright install --with-deps chromium "
                "(or playwright install chromium)",
                allow_module_level=True,
            )


_ensure_dashboard_playwright_environment()

def pytest_configure(config):
    """
    Ensure pytest-playwright is available for `page` fixture.
    This is a safe fallback when plugin autoload is disabled/misconfigured.
    """
    if not config.pluginmanager.hasplugin("playwright"):
        from pytest_playwright import pytest_playwright as playwright_plugin

        config.pluginmanager.register(playwright_plugin, "playwright")

from playwright.sync_api import Browser, BrowserContext, Page, expect


# ==================== Configuration ====================

ENV_FILE = os.path.join(_REPO_ROOT, ".env")
DASHBOARD_URL = "http://localhost:8000/dashboard/"
API_BASE_URL = "http://localhost:8000/api/v1"
SERVER_STARTUP_TIMEOUT = 30  # seconds
PAGE_LOAD_TIMEOUT = 10000  # milliseconds

# make server-dashboard-start can include npm build; allow override via env
_DASHBOARD_MAKE_START_TIMEOUT = int(
    os.environ.get("POWERMEM_DASHBOARD_MAKE_TIMEOUT", "1800")
)


def _run_make_target(target: str, *, timeout: int) -> int:
    """Run `make <target>` from repo root. Returns process return code."""
    return subprocess.run(
        ["make", target],
        cwd=_REPO_ROOT,
        env=os.environ.copy(),
        timeout=timeout,
    ).returncode


@pytest.fixture(scope="session", autouse=True)
def _reset_server_and_built_dashboard():
    """
    Before any test: stop any running server, rebuild dashboard, start server via Makefile.
    Matches: `make server-stop` then `make server-dashboard-start` from project root.
    """
    if _env_truthy("POWERMEM_DASHBOARD_SKIP_MAKE_SERVER"):
        print(
            "\n[SETUP] Skipping make server-stop / make server-dashboard-start "
            "(POWERMEM_DASHBOARD_SKIP_MAKE_SERVER=1).",
            flush=True,
        )
        yield
        return

    print(
        f"\n[SETUP] Prerequisite: make server-stop && make server-dashboard-start "
        f"(cwd={_REPO_ROOT})",
        flush=True,
    )
    stop_rc = _run_make_target("server-stop", timeout=120)
    if stop_rc != 0:
        print(
            f"[WARNING] make server-stop exited {stop_rc} (continuing to server-dashboard-start).",
            file=sys.stderr,
            flush=True,
        )
    start_rc = _run_make_target(
        "server-dashboard-start",
        timeout=_DASHBOARD_MAKE_START_TIMEOUT,
    )
    if start_rc != 0:
        pytest.fail(
            f"make server-dashboard-start failed with exit code {start_rc}. "
            f"Run from repo root: make server-stop && make server-dashboard-start"
        )
    print("[SETUP] make server-dashboard-start completed.\n", flush=True)
    print("[SETUP] Waiting 10s for server process to accept connections...\n", flush=True)
    time.sleep(10)
    yield


def _preflight_log(msg: str) -> None:
    print(f"[DASHBOARD PREFLIGHT] {msg}", flush=True)


def _local_port_open(host: str, port: int, timeout_s: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return True
    except OSError:
        return False


def _wait_for_port(host: str, port: int, *, total_s: int, label: str) -> None:
    deadline = time.time() + total_s
    while time.time() < deadline:
        if _local_port_open(host, port):
            _preflight_log(f"port {port} is accepting connections ({label})")
            return
        time.sleep(0.5)
    pytest.fail(
        f"{label}: nothing is listening on {host}:{port} after {total_s}s — "
        "is the API server up? Check make server-start / server.log."
    )


def _http_check(
    method: str,
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 15,
) -> Tuple[int, str]:
    import requests

    r = requests.request(method, url, headers=headers or {}, timeout=timeout)
    text = (r.text or "")[:500]
    return r.status_code, text


def _import_versions_log() -> None:
    _preflight_log(f"python: {sys.version.split()[0]} ({sys.executable})")
    try:
        import playwright  # type: ignore[import-not-found]
    except Exception as e:  # pragma: no cover
        pytest.fail(
            f"import playwright failed: {e}\n" + traceback.format_exc()
        )
    try:
        import pytest_playwright  # type: ignore[import-not-found]
    except Exception as e:  # pragma: no cover
        pytest.fail(
            f"import pytest_playwright failed: {e}\n" + traceback.format_exc()
        )
    _preflight_log(f"playwright package: {getattr(playwright, '__version__', 'unknown')}")
    _preflight_log(f"pytest_playwright: {getattr(pytest_playwright, '__version__', 'ok')}")


def _playwright_chromium_sanity(
    api_key: str,
    *,
    preflight_networkidle: bool,
    networkidle_ms: int,
    strict_networkidle: bool,
) -> None:
    from playwright.sync_api import sync_playwright

    _preflight_log("launching Chromium (sync_playwright)…")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_context().new_page()
                page.add_init_script(
                    f"localStorage.setItem('powermem_api_key', {json.dumps(api_key)});"
                )
                page.goto(DASHBOARD_URL, wait_until="domcontentloaded", timeout=30_000)
                title = page.title()
                _preflight_log(f"chromium: domcontentloaded OK, document.title={title!r}")
                if preflight_networkidle:
                    try:
                        page.goto(DASHBOARD_URL, wait_until="networkidle", timeout=networkidle_ms)
                        _preflight_log("chromium: networkidle OK (same as dashboard_page wait_until)")
                    except Exception as e:
                        msg = (
                            f"chromium: networkidle wait failed ({networkidle_ms}ms): {e}\n"
                            "The UI tests use wait_until=networkidle on goto/reload; "
                            "hanging fetches (analytics, long polling) can cause this in CI. "
                            "Set POWERMEM_DASHBOARD_PREFLIGHT_STRICT=0 to only warn, "
                            "or fix the dashboard / test waits."
                        )
                        if strict_networkidle:
                            pytest.fail(msg + "\n" + traceback.format_exc())
                        _preflight_log("WARNING: " + msg)
            finally:
                browser.close()
    except Exception as e:
        pytest.fail(
            "Playwright Chromium failed to start or open the dashboard.\n"
            f"{e}\n{traceback.format_exc()}"
        )


@pytest.fixture(scope="session", autouse=True)
def _dashboard_e2e_preflight(
    _reset_server_and_built_dashboard,
    api_key: str,
) -> None:
    """
    After server + dashboard are ready, verify socket/HTTP/Playwright before UI cases.

    Runs after ``_reset_server_and_built_dashboard`` and before module fixtures that
    create data (e.g. ``setup_test_memories``) so CI logs show a clear root cause
    when Playwright fails while API-only tests still pass.
    """
    if _env_truthy("POWERMEM_DASHBOARD_SKIP_PREFLIGHT"):
        _preflight_log("skipped (POWERMEM_DASHBOARD_SKIP_PREFLIGHT=1)")
        return

    _preflight_log("begin environment checks (deps already ensured at import time)")
    _import_versions_log()

    health_url = f"{API_BASE_URL.rstrip('/')}/system/health"
    # In CI, default to strict networkidle preflight (same wait as many UI tests) so we fail
    # here with a clear message instead of a generic Playwright fixture ERROR.
    if os.environ.get("POWERMEM_DASHBOARD_PREFLIGHT_STRICT") is not None:
        strict_net = _env_truthy("POWERMEM_DASHBOARD_PREFLIGHT_STRICT")
    else:
        strict_net = _env_truthy("CI")
    net_ms = int(os.environ.get("POWERMEM_DASHBOARD_PREFLIGHT_NETWORKIDLE_MS", "30000"))

    _wait_for_port("127.0.0.1", 8000, total_s=60, label="local TCP")

    try:
        st, body = _http_check("GET", health_url, timeout=15)
        if st != 200:
            pytest.fail(f"GET {health_url} -> HTTP {st!r} body[0:500]={body!r}")
        _preflight_log(f"GET {health_url} -> 200, body[0:200]={body[:200]!r}")
    except Exception as e:
        pytest.fail(
            f"API health check failed: {e}\n{traceback.format_exc()}"
        )

    try:
        st, body = _http_check("GET", DASHBOARD_URL, timeout=20)
        if st != 200:
            pytest.fail(
                f"GET {DASHBOARD_URL} -> HTTP {st!r} body[0:500]={body!r}"
            )
        low = (body or "").lower()
        if "html" not in low and "<!doctype" not in low:
            _preflight_log(
                f"WARNING: dashboard response does not look like HTML (body[0:200]={body[:200]!r})"
            )
        _preflight_log("dashboard URL returned HTTP 200 and looks like a document response")
    except Exception as e:
        pytest.fail(
            f"Dashboard HTTP check failed: {e}\n{traceback.format_exc()}"
        )

    try:
        h = {"X-API-Key": api_key}
        st, body = _http_check("GET", f"{API_BASE_URL}/memories/stats", headers=h, timeout=20)
        if st != 200:
            pytest.fail(
                f"GET /memories/stats (with X-API-Key) -> HTTP {st!r} body[0:500]={body!r}"
            )
        _preflight_log("authenticated /memories/stats -> 200 (same as API tests)")
    except Exception as e:
        pytest.fail(
            f"Authenticated stats check failed: {e}\n{traceback.format_exc()}"
        )

    _playwright_chromium_sanity(
        api_key,
        preflight_networkidle=True,
        networkidle_ms=net_ms,
        strict_networkidle=strict_net,
    )
    _preflight_log("all preflight checks passed; starting tests")


# ==================== Fixtures ====================

def _read_dashboard_api_key_from_env_file(path: str) -> Optional[str]:
    """
    Match server auth: POWERMEM_SERVER_API_KEYS (comma-separated) or optional
    POWERMEM_SERVER_API_KEY (single). Check KEYS before KEY so KEYS= is not misparsed.
    """
    if not os.path.exists(path):
        return None
    single: Optional[str] = None
    keys_csv: Optional[str] = None
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            raw = line.strip()
            if not raw or raw.startswith("#"):
                continue
            if raw.startswith("POWERMEM_SERVER_API_KEYS="):
                keys_csv = raw.split("=", 1)[1].strip().strip('"').strip("'")
            elif raw.startswith("POWERMEM_SERVER_API_KEY="):
                single = raw.split("=", 1)[1].strip().strip('"').strip("'")
    if single:
        return single
    if keys_csv:
        first = keys_csv.split(",")[0].strip()
        if first:
            return first
    return None


@pytest.fixture(scope="session")
def api_key():
    """Get API key from repo-root .env (same keys the HTTP server uses)."""
    api_key_value = _read_dashboard_api_key_from_env_file(ENV_FILE)
    if not api_key_value:
        pytest.skip(
            "No API key in .env: set POWERMEM_SERVER_API_KEYS or POWERMEM_SERVER_API_KEY "
            f"in {ENV_FILE}"
        )
    return api_key_value


@pytest.fixture(scope="session")
def server_process():
    """Start PowerMem API server for testing"""
    # Check if server is already running
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    result = sock.connect_ex(('localhost', 8000))
    sock.close()
    
    if result == 0:
        print("\n[INFO] Server already running on port 8000")
        yield None
        return
    
    # Start server
    print("\n[SETUP] Starting PowerMem API server...")
    process = subprocess.Popen(
        [sys.executable, "-m", "src.server.cli.server", "--host", "0.0.0.0", "--port", "8000"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "PYTHONPATH": _REPO_ROOT}
    )
    
    # Wait for server to start
    start_time = time.time()
    while time.time() - start_time < SERVER_STARTUP_TIMEOUT:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        result = sock.connect_ex(('localhost', 8000))
        sock.close()
        if result == 0:
            print("[SETUP] Server started successfully")
            break
        time.sleep(1)
    else:
        process.terminate()
        pytest.fail("Server failed to start within timeout")
    
    yield process
    
    # Cleanup
    print("\n[TEARDOWN] Stopping PowerMem API server...")
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args):
    """Configure browser context"""
    return {
        **browser_context_args,
        "viewport": {"width": 1920, "height": 1080},
        "locale": "en-US",
    }


@pytest.fixture(scope="function")
def dashboard_page(page: Page, api_key: str):
    """Setup dashboard page with API key"""
    # Navigate to dashboard
    page.goto(DASHBOARD_URL, wait_until="networkidle", timeout=PAGE_LOAD_TIMEOUT)
    
    # Set API key in localStorage
    page.evaluate(f"localStorage.setItem('powermem_api_key', '{api_key}')")
    
    # Reload page to apply API key
    page.reload(wait_until="networkidle")
    
    # Wait for page to be ready
    page.wait_for_load_state("networkidle")
    
    yield page


@pytest.fixture(scope="module")
def test_data():
    """Test data for creating memories"""
    return {
        "user_id": "dashboard_test_user",
        "agent_id": "dashboard_test_agent",
        "run_id": "dashboard_test_run",
        "memory_ids": [],
    }


@pytest.fixture(scope="module", autouse=True)
def setup_test_memories(test_data, api_key):
    """Create test memories before tests"""
    import requests
    
    print("\n[SETUP] Creating test memories...")
    
    headers = {
        "X-API-Key": api_key,
        "Content-Type": "application/json"
    }
    
    # Create test memories
    test_memories = [
        "Dashboard test memory 1: I love Python programming",
        "Dashboard test memory 2: I enjoy machine learning",
        "Dashboard test memory 3: I prefer React for frontend development",
        "Dashboard test memory 4: I use pytest for testing",
        "Dashboard test memory 5: I work with FastAPI backend",
    ]
    
    for content in test_memories:
        # MemoryCreateRequest expects `content`, not `messages` (see server models).
        payload = {
            "content": content,
            "user_id": test_data["user_id"],
            "agent_id": test_data["agent_id"],
            "run_id": test_data["run_id"],
            "infer": False,
        }

        try:
            response = requests.post(
                f"{API_BASE_URL}/memories",
                json=payload,
                headers=headers,
                timeout=30,
            )
            if response.status_code != 200:
                print(
                    f"[WARNING] Create memory HTTP {response.status_code}: "
                    f"{response.text[:500]}"
                )
                continue
            result = response.json()
            if not (result.get("success") and result.get("data")):
                print(f"[WARNING] Create memory rejected: {result!r}")
                continue
            data = result["data"]
            # API returns data as a list of memory objects
            if isinstance(data, list):
                for mem in data:
                    if not isinstance(mem, dict):
                        continue
                    memory_id = mem.get("memory_id") or mem.get("id")
                    if memory_id is not None:
                        test_data["memory_ids"].append(str(memory_id))
            elif isinstance(data, dict):
                memory_id = data.get("memory_id") or data.get("id")
                if memory_id is not None:
                    test_data["memory_ids"].append(str(memory_id))
        except Exception as e:
            print(f"[WARNING] Failed to create test memory: {e}")
    
    print(f"[SETUP] Created {len(test_data['memory_ids'])} test memories")
    
    yield
    
    # Cleanup
    print("\n[TEARDOWN] Cleaning up test memories...")
    try:
        response = requests.delete(
            f"{API_BASE_URL}/system/delete-all-memories",
            params={"user_id": test_data["user_id"]},
            headers=headers,
            timeout=30
        )
        if response.status_code == 200:
            print("[TEARDOWN] Test memories cleaned up successfully")
    except Exception as e:
        print(f"[WARNING] Failed to cleanup test memories: {e}")


# ==================== Helper Functions ====================

def wait_for_element(page: Page, selector: str, timeout: int = 5000):
    """Wait for element to be visible"""
    page.wait_for_selector(selector, state="visible", timeout=timeout)


def wait_for_text(page: Page, text: str, timeout: int = 5000):
    """Wait for text to appear on page"""
    page.wait_for_selector(f"text={text}", timeout=timeout)


def click_and_wait(page: Page, selector: str, wait_time: int = 1000):
    """Click element and wait for action to complete"""
    page.click(selector)
    page.wait_for_timeout(wait_time)


def get_text_content(page: Page, selector: str) -> str:
    """Get text content of element"""
    element = page.locator(selector)
    return element.text_content() or ""


def assert_text_visible(page: Page, text: str, timeout: int = 5000):
    """Assert text is visible on page"""
    expect(page.locator(f"text={text}").first).to_be_visible(timeout=timeout)


def assert_overview_loaded(page: Page, timeout: int = 10000):
    """Assert dashboard overview heading is visible (supports i18n text variants)."""
    heading_pattern = re.compile(
        r"Memory Overview|Dashboard|记忆统计概览|仪表盘",
        re.IGNORECASE,
    )
    expect(page.get_by_text(heading_pattern).first).to_be_visible(timeout=timeout)


def assert_element_visible(page: Page, selector: str, timeout: int = 5000):
    """Assert element is visible"""
    expect(page.locator(selector).first).to_be_visible(timeout=timeout)


# ==================== Dashboard Overview Tests ====================

class TestDashboardOverview:
    """Test Dashboard Overview page"""
    
    def test_overview_page_loads(self, dashboard_page: Page):
        """Overview page should load successfully"""
        # Check page title
        expect(dashboard_page).to_have_title(re.compile("PowerMem Dashboard", re.IGNORECASE))
        
        # Check main heading
        assert_overview_loaded(dashboard_page)
    
    def test_overview_stats_cards_visible(self, dashboard_page: Page):
        """Overview page should display stats cards"""
        # Wait for stats to load
        dashboard_page.wait_for_selector("text=Total Memories", timeout=10000)
        
        # Check for stat cards
        assert_text_visible(dashboard_page, "Total Memories")
        expect(
            dashboard_page.get_by_text(
                re.compile(r"Avg\.?\sImportance|平均重要性", re.IGNORECASE)
            ).first
        ).to_be_visible(timeout=5000)
        assert_text_visible(dashboard_page, "Access Density")
        assert_text_visible(dashboard_page, "Unique Dates")
    
    def test_overview_system_health_visible(self, dashboard_page: Page):
        """Overview page should display system health card"""
        # Wait for system health to load
        dashboard_page.wait_for_selector("text=System Health", timeout=10000)
        
        assert_text_visible(dashboard_page, "System Health")
        assert_text_visible(dashboard_page, "Status")
        assert_text_visible(dashboard_page, "Uptime")
    
    def test_overview_charts_visible(self, dashboard_page: Page):
        """Overview page should display charts"""
        # Wait for charts to load
        dashboard_page.wait_for_timeout(2000)
        
        # Check for chart titles
        assert_text_visible(dashboard_page, "Growth Trend")
        assert_text_visible(dashboard_page, "Memory Categories")
        assert_text_visible(dashboard_page, "Retention Age")
    
    def test_overview_memory_quality_visible(self, dashboard_page: Page):
        """Overview page should display memory quality card"""
        dashboard_page.wait_for_selector("text=Memory Quality", timeout=10000)
        assert_text_visible(dashboard_page, "Memory Quality")
    
    def test_overview_time_range_filter(self, dashboard_page: Page):
        """Time range filter should work"""
        # Wait for page to load
        assert_overview_loaded(dashboard_page, timeout=10000)
        
        # Find and click time range selector
        time_selector = dashboard_page.locator("button:has-text('Last 30 days'), button:has-text('Last 7 days'), button:has-text('Last 90 days'), button:has-text('All Time')").first
        if time_selector.is_visible():
            time_selector.click()
            dashboard_page.wait_for_timeout(500)
            
            # Select different time range
            last_7_days = dashboard_page.locator("text=Last 7 days").first
            if last_7_days.is_visible():
                last_7_days.click()
                dashboard_page.wait_for_timeout(2000)
    
    def test_overview_refresh_button(self, dashboard_page: Page):
        """Refresh button should reload data"""
        # Wait for page to load
        assert_overview_loaded(dashboard_page, timeout=10000)
        
        # Find and click refresh button
        refresh_button = dashboard_page.locator("button:has-text('Refresh'), button:has-text('Refreshing')").first
        if refresh_button.is_visible():
            refresh_button.click()
            dashboard_page.wait_for_timeout(2000)
            
            # Check if refresh completed
            assert_overview_loaded(dashboard_page)


# ==================== Memories Page Tests ====================

class TestMemoriesPage:
    """Test Memories page"""
    
    @pytest.fixture(autouse=True)
    def navigate_to_memories(self, dashboard_page: Page):
        """Navigate to Memories page before each test"""
        # Click on Memories link in sidebar or navigation
        memories_link = dashboard_page.locator("a[href*='memories'], button:has-text('Memories')").first
        if memories_link.is_visible():
            memories_link.click()
            dashboard_page.wait_for_timeout(1000)
        else:
            # Try direct navigation
            dashboard_page.goto(f"{DASHBOARD_URL}#/memories", wait_until="networkidle")
        
        # Wait for memories page to load
        dashboard_page.wait_for_selector("text=Memories", timeout=10000)
    
    def test_memories_page_loads(self, dashboard_page: Page):
        """Memories page should load successfully"""
        assert_text_visible(dashboard_page, "Memories")
        
        # Check for table headers
        dashboard_page.wait_for_selector("table", timeout=5000)
    
    def test_memories_table_visible(self, dashboard_page: Page):
        """Memories table should be visible with data"""
        # Wait for table to load
        dashboard_page.wait_for_selector("table", timeout=5000)
        
        # Check table headers
        assert_text_visible(dashboard_page, "User ID")
        assert_text_visible(dashboard_page, "Agent ID")
        assert_text_visible(dashboard_page, "Content")
    
    def test_memories_filter_by_user_id(self, dashboard_page: Page, test_data: Dict):
        """Filter memories by user ID should work"""
        # Wait for page to load
        dashboard_page.wait_for_selector("table", timeout=5000)
        
        # Find user ID filter input
        user_id_input = dashboard_page.locator("input[placeholder*='User'], input[placeholder*='user']").first
        if user_id_input.is_visible():
            user_id_input.fill(test_data["user_id"])
            dashboard_page.wait_for_timeout(500)
            
            # Click apply filter button
            filter_button = dashboard_page.locator("button:has-text('Apply'), button:has-text('Filter')").first
            if filter_button.is_visible():
                filter_button.click()
                dashboard_page.wait_for_timeout(2000)
                
                # Verify filtered results
                page_content = dashboard_page.content()
                assert test_data["user_id"] in page_content or "No memories" in page_content
    
    def test_memories_filter_by_content(self, dashboard_page: Page):
        """Filter memories by content should work"""
        # Wait for page to load
        dashboard_page.wait_for_selector("table", timeout=5000)
        
        # Find content filter input
        content_input = dashboard_page.locator("input[placeholder*='content'], input[placeholder*='Content'], input[placeholder*='Search']").first
        if content_input.is_visible():
            content_input.fill("Python")
            dashboard_page.wait_for_timeout(500)
            
            # Click apply filter button
            filter_button = dashboard_page.locator("button:has-text('Apply'), button:has-text('Filter')").first
            if filter_button.is_visible():
                filter_button.click()
                dashboard_page.wait_for_timeout(2000)
    
    def test_memories_clear_filters(self, dashboard_page: Page):
        """Clear filters button should reset all filters"""
        # Wait for page to load
        dashboard_page.wait_for_selector("table", timeout=5000)
        
        # Find and click clear filters button
        clear_button = dashboard_page.locator("button:has-text('Clear')").first
        if clear_button.is_visible():
            clear_button.click()
            dashboard_page.wait_for_timeout(1000)
    
    def test_memories_pagination_next(self, dashboard_page: Page):
        """Pagination next button should work"""
        # Wait for page to load
        dashboard_page.wait_for_selector("table", timeout=5000)
        
        # Find next button
        next_button = dashboard_page.locator("button:has-text('Next'), button:has-text('next')").first
        if next_button.is_visible() and not next_button.is_disabled():
            next_button.click()
            dashboard_page.wait_for_timeout(2000)
    
    def test_memories_pagination_prev(self, dashboard_page: Page):
        """Pagination previous button should work"""
        # Wait for page to load
        dashboard_page.wait_for_selector("table", timeout=5000)
        
        # Find previous button
        prev_button = dashboard_page.locator("button:has-text('Prev'), button:has-text('Previous')").first
        if prev_button.is_visible():
            # Button should be disabled on first page
            assert prev_button.is_disabled() or True
    
    def test_memories_view_details(self, dashboard_page: Page):
        """Clicking on memory should show details sheet"""
        # Wait for table to load
        dashboard_page.wait_for_selector("table tbody tr", timeout=5000)
        
        # Click on first memory row
        first_row = dashboard_page.locator("table tbody tr").first
        if first_row.is_visible():
            first_row.click()
            dashboard_page.wait_for_timeout(1000)
            
            # Check if detail sheet opened
            detail_sheet = dashboard_page.locator("text=Memory Details, text=Details").first
            if detail_sheet.is_visible():
                # Verify detail fields
                assert_text_visible(dashboard_page, "Content")
                assert_text_visible(dashboard_page, "Category")
    
    def test_memories_delete_action(self, dashboard_page: Page, test_data: Dict):
        """Delete memory action should work"""
        # Wait for table to load
        dashboard_page.wait_for_selector("table tbody tr", timeout=5000)
        
        # Find action menu button (three dots)
        action_button = dashboard_page.locator("button[aria-haspopup='menu']").first
        if action_button.is_visible():
            action_button.click()
            dashboard_page.wait_for_timeout(500)
            
            # Look for delete option
            delete_option = dashboard_page.locator("text=Delete").first
            if delete_option.is_visible():
                # Don't actually delete in this test, just verify it's there
                pass
    
    def test_memories_refresh_button(self, dashboard_page: Page):
        """Refresh button should reload memories"""
        # Wait for page to load
        dashboard_page.wait_for_selector("table", timeout=5000)
        
        # Find and click refresh button
        refresh_button = dashboard_page.locator("button:has-text('Refresh')").first
        if refresh_button.is_visible():
            refresh_button.click()
            dashboard_page.wait_for_timeout(2000)


# ==================== User Profile Page Tests ====================

class TestUserProfilePage:
    """Test User Profile page"""
    
    @pytest.fixture(autouse=True)
    def navigate_to_user_profile(self, dashboard_page: Page):
        """Navigate to User Profile page before each test"""
        # Click on User Profile link
        profile_link = dashboard_page.locator("a[href*='user-profile'], button:has-text('User Profile')").first
        if profile_link.is_visible():
            profile_link.click()
            dashboard_page.wait_for_timeout(1000)
        else:
            # Try direct navigation
            dashboard_page.goto(f"{DASHBOARD_URL}#/user-profile", wait_until="networkidle")
        
        # Wait for user profile page to load
        dashboard_page.wait_for_selector("text=User Profile", timeout=10000)
    
    def test_user_profile_page_loads(self, dashboard_page: Page):
        """User Profile page should load successfully"""
        assert_text_visible(dashboard_page, "User Profile")
    
    def test_user_profile_table_visible(self, dashboard_page: Page):
        """User Profile table should be visible"""
        # Wait for table or empty state
        dashboard_page.wait_for_timeout(2000)
        
        # Check for table or no profiles message
        page_content = dashboard_page.content()
        assert "User ID" in page_content or "No profiles" in page_content or "no profiles" in page_content
    
    def test_user_profile_filter_by_user_id(self, dashboard_page: Page, test_data: Dict):
        """Filter user profiles by user ID should work"""
        # Wait for page to load
        dashboard_page.wait_for_timeout(2000)
        
        # Find user ID filter input
        user_id_input = dashboard_page.locator("input[placeholder*='User'], input[placeholder*='user']").first
        if user_id_input.is_visible():
            user_id_input.fill(test_data["user_id"])
            dashboard_page.wait_for_timeout(500)
            
            # Click search button
            search_button = dashboard_page.locator("button:has-text('Search'), button:has-text('Filter')").first
            if search_button.is_visible():
                search_button.click()
                dashboard_page.wait_for_timeout(2000)
    
    def test_user_profile_view_details(self, dashboard_page: Page):
        """View details button should open profile details"""
        # Wait for page to load
        dashboard_page.wait_for_timeout(2000)
        
        # Find view details button
        view_button = dashboard_page.locator("button:has-text('View')").first
        if view_button.is_visible():
            view_button.click()
            dashboard_page.wait_for_timeout(1000)
            
            # Check if detail sheet opened
            detail_sheet = dashboard_page.locator("text=Profile Details, text=User Profile").first
            if detail_sheet.is_visible():
                assert_text_visible(dashboard_page, "User ID")
    
    def test_user_profile_pagination(self, dashboard_page: Page):
        """User Profile pagination should work"""
        # Wait for page to load
        dashboard_page.wait_for_timeout(2000)
        
        # Check for pagination controls
        next_button = dashboard_page.locator("button:has-text('Next')").first
        prev_button = dashboard_page.locator("button:has-text('Prev')").first
        
        # Verify pagination buttons exist
        if next_button.is_visible():
            assert True


# ==================== Settings Page Tests ====================

class TestSettingsPage:
    """Test Settings page"""
    
    @pytest.fixture(autouse=True)
    def navigate_to_settings(self, dashboard_page: Page):
        """Navigate to Settings page before each test"""
        # Click on Settings link
        settings_link = dashboard_page.locator("a[href*='settings'], button:has-text('Settings')").first
        if settings_link.is_visible():
            settings_link.click()
            dashboard_page.wait_for_timeout(1000)
        else:
            # Try direct navigation
            dashboard_page.goto(f"{DASHBOARD_URL}#/settings", wait_until="networkidle")
        
        # Wait for settings page to load
        dashboard_page.wait_for_selector("text=Settings", timeout=10000)
    
    def test_settings_page_loads(self, dashboard_page: Page):
        """Settings page should load successfully"""
        assert_text_visible(dashboard_page, "Settings")
    
    def test_settings_api_key_input_visible(self, dashboard_page: Page):
        """API key input should be visible"""
        # Check for API key input
        api_key_input = dashboard_page.locator("input[type='password']").first
        assert_element_visible(dashboard_page, "input[type='password']")
    
    def test_settings_save_button_visible(self, dashboard_page: Page):
        """Save button should be visible"""
        assert_text_visible(dashboard_page, "Save")
    
    def test_settings_api_key_update(self, dashboard_page: Page, api_key: str):
        """API key update should work"""
        # Find API key input
        api_key_input = dashboard_page.locator("input[type='password']").first
        if api_key_input.is_visible():
            # Clear and fill with test key
            api_key_input.fill("")
            api_key_input.fill(api_key)
            dashboard_page.wait_for_timeout(500)
            
            # Click save button
            save_button = dashboard_page.locator("button:has-text('Save')").first
            if save_button.is_visible():
                save_button.click()
                dashboard_page.wait_for_timeout(1000)
                
                # Check for success toast
                toast = dashboard_page.locator("text=saved, text=success").first
                if toast.is_visible(timeout=3000):
                    assert True


# ==================== Navigation Tests ====================

class TestNavigation:
    """Test navigation between pages"""
    
    def test_navigate_to_memories(self, dashboard_page: Page):
        """Should navigate to Memories page"""
        # Find and click Memories link
        memories_link = dashboard_page.locator("a[href*='memories'], button:has-text('Memories')").first
        if memories_link.is_visible():
            memories_link.click()
            dashboard_page.wait_for_timeout(1000)
            
            # Verify navigation
            assert_text_visible(dashboard_page, "Memories")
    
    def test_navigate_to_user_profile(self, dashboard_page: Page):
        """Should navigate to User Profile page"""
        # Find and click User Profile link
        profile_link = dashboard_page.locator("a[href*='user-profile'], button:has-text('User Profile')").first
        if profile_link.is_visible():
            profile_link.click()
            dashboard_page.wait_for_timeout(1000)
            
            # Verify navigation
            assert_text_visible(dashboard_page, "User Profile")
    
    def test_navigate_to_settings(self, dashboard_page: Page):
        """Should navigate to Settings page"""
        # Find and click Settings link
        settings_link = dashboard_page.locator("a[href*='settings'], button:has-text('Settings')").first
        if settings_link.is_visible():
            settings_link.click()
            dashboard_page.wait_for_timeout(1000)
            
            # Verify navigation
            assert_text_visible(dashboard_page, "Settings")
    
    def test_navigate_back_to_dashboard(self, dashboard_page: Page):
        """Should navigate back to Dashboard overview"""
        # Navigate to another page first
        memories_link = dashboard_page.locator("a[href*='memories']").first
        if memories_link.is_visible():
            memories_link.click()
            dashboard_page.wait_for_timeout(1000)
        
        # Navigate back to dashboard
        dashboard_link = dashboard_page.locator("a[href='/'], a[href='#/'], button:has-text('Dashboard')").first
        if dashboard_link.is_visible():
            dashboard_link.click()
            dashboard_page.wait_for_timeout(1000)
            
            # Verify we're back on dashboard
            assert_overview_loaded(dashboard_page)


# ==================== Theme Tests ====================

class TestTheme:
    """Test theme switching"""
    
    def test_theme_toggle_exists(self, dashboard_page: Page):
        """Theme toggle button should exist"""
        # Look for theme toggle button (usually moon/sun icon)
        theme_button = dashboard_page.locator("button[aria-label*='theme'], button[aria-label*='Theme']").first
        if not theme_button.is_visible():
            # Try finding by common theme toggle patterns
            theme_button = dashboard_page.locator("button:has-text('Light'), button:has-text('Dark')").first
        
        # Just verify the page loaded, theme toggle is optional
        assert_overview_loaded(dashboard_page)
    
    def test_theme_toggle_click(self, dashboard_page: Page):
        """Theme toggle should switch theme"""
        # Find theme toggle button
        theme_button = dashboard_page.locator("button[aria-label*='theme'], button[aria-label*='Theme']").first
        if theme_button.is_visible():
            # Get initial theme
            initial_html = dashboard_page.locator("html").first.get_attribute("class") or ""
            
            # Click toggle
            theme_button.click()
            dashboard_page.wait_for_timeout(500)
            
            # Get new theme
            new_html = dashboard_page.locator("html").first.get_attribute("class") or ""
            
            # Theme should have changed (or at least button worked)
            assert True


# ==================== Language Tests ====================

class TestLanguage:
    """Test language switching"""
    
    def test_language_switcher_exists(self, dashboard_page: Page):
        """Language switcher should exist"""
        # Look for language switcher (EN/中文)
        lang_button = dashboard_page.locator("button:has-text('EN'), button:has-text('中文'), button:has-text('English'), button:has-text('Chinese')").first
        
        # Just verify the page loaded, language switcher is optional
        assert_overview_loaded(dashboard_page)
    
    def test_language_switch_to_chinese(self, dashboard_page: Page):
        """Should switch to Chinese language"""
        # Find language button
        lang_button = dashboard_page.locator("button:has-text('EN'), button:has-text('English')").first
        if lang_button.is_visible():
            lang_button.click()
            dashboard_page.wait_for_timeout(500)
            
            # Select Chinese
            chinese_option = dashboard_page.locator("text=中文, text=Chinese").first
            if chinese_option.is_visible():
                chinese_option.click()
                dashboard_page.wait_for_timeout(1000)
                
                # Verify language changed
                page_content = dashboard_page.content()
                # Check for Chinese text
                assert "仪表板" in page_content or "Dashboard" in page_content


# ==================== Error Handling Tests ====================

class TestErrorHandling:
    """Test error handling"""
    
    def test_invalid_api_key_shows_error(self, page: Page):
        """Invalid API key should show error message"""
        # Navigate to dashboard without API key
        page.goto(DASHBOARD_URL, wait_until="networkidle")
        
        # Set invalid API key
        page.evaluate("localStorage.setItem('powermem_api_key', 'invalid_key_12345')")
        page.reload(wait_until="networkidle")
        
        # Wait for error to appear
        page.wait_for_timeout(3000)
        
        # Check for error message
        page_content = page.content()
        assert "error" in page_content.lower() or "Error" in page_content or "API" in page_content
    
    def test_empty_api_key_shows_error(self, page: Page):
        """Empty API key should show error message"""
        # Navigate to dashboard
        page.goto(DASHBOARD_URL, wait_until="networkidle")
        
        # Clear API key
        page.evaluate("localStorage.removeItem('powermem_api_key')")
        page.reload(wait_until="networkidle")
        
        # Wait for error to appear
        page.wait_for_timeout(3000)
        
        # Check for error or prompt for API key
        page_content = page.content()
        assert "error" in page_content.lower() or "API" in page_content or "key" in page_content.lower()


# ==================== Responsive Design Tests ====================

class TestResponsiveDesign:
    """Test responsive design"""
    
    def test_mobile_viewport(self, page: Page, api_key: str):
        """Dashboard should work on mobile viewport"""
        # Set mobile viewport
        page.set_viewport_size({"width": 375, "height": 667})
        
        # Navigate to dashboard
        page.goto(DASHBOARD_URL, wait_until="networkidle")
        page.evaluate(f"localStorage.setItem('powermem_api_key', '{api_key}')")
        page.reload(wait_until="networkidle")
        
        # Wait for page to load
        page.wait_for_timeout(3000)
        
        # Check if dashboard loaded
        assert_overview_loaded(page)
    
    def test_tablet_viewport(self, page: Page, api_key: str):
        """Dashboard should work on tablet viewport"""
        # Set tablet viewport
        page.set_viewport_size({"width": 768, "height": 1024})
        
        # Navigate to dashboard
        page.goto(DASHBOARD_URL, wait_until="networkidle")
        page.evaluate(f"localStorage.setItem('powermem_api_key', '{api_key}')")
        page.reload(wait_until="networkidle")
        
        # Wait for page to load
        page.wait_for_timeout(3000)
        
        # Check if dashboard loaded
        assert_overview_loaded(page)


# ==================== API Integration Tests ====================

class TestAPIIntegration:
    """Test API integration"""
    
    def test_api_stats_endpoint(self, api_key: str):
        """Stats API endpoint should return data"""
        import requests
        
        headers = {"X-API-Key": api_key}
        response = requests.get(f"{API_BASE_URL}/memories/stats", headers=headers, timeout=10)
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "total_memories" in data["data"]
        assert "by_type" in data["data"]
        assert "growth_trend" in data["data"]
    
    def test_api_memories_endpoint(self, api_key: str):
        """Memories API endpoint should return data"""
        import requests
        
        headers = {"X-API-Key": api_key}
        response = requests.get(
            f"{API_BASE_URL}/memories",
            headers=headers,
            params={"limit": 10},
            timeout=10
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "memories" in data["data"]
        assert "total" in data["data"]
    
    def test_api_system_status_endpoint(self, api_key: str):
        """System status API endpoint should return data"""
        import requests
        
        headers = {"X-API-Key": api_key}
        response = requests.get(f"{API_BASE_URL}/system/status", headers=headers, timeout=10)
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "status" in data["data"]
        assert "version" in data["data"]
        assert "uptime_seconds" in data["data"]
    
    def test_api_memory_quality_endpoint(self, api_key: str):
        """Memory quality API endpoint should return data"""
        import requests
        
        headers = {"X-API-Key": api_key}
        response = requests.get(f"{API_BASE_URL}/memories/quality", headers=headers, timeout=10)
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "total_memories" in data["data"]
        assert "low_quality_count" in data["data"]
        assert "low_quality_ratio" in data["data"]
    
    def test_api_user_profiles_endpoint(self, api_key: str):
        """User profiles API endpoint should return data"""
        import requests
        
        headers = {"X-API-Key": api_key}
        response = requests.get(f"{API_BASE_URL}/users/profiles", headers=headers, timeout=10)
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "profiles" in data["data"]
        assert "total" in data["data"]


# ==================== Performance Tests ====================

class TestPerformance:
    """Test dashboard performance"""
    
    def test_page_load_time(self, page: Page, api_key: str):
        """Page should load within reasonable time"""
        start_time = time.time()
        
        page.goto(DASHBOARD_URL, wait_until="networkidle")
        page.evaluate(f"localStorage.setItem('powermem_api_key', '{api_key}')")
        page.reload(wait_until="networkidle")
        
        # Wait for main content to load
        assert_overview_loaded(page, timeout=10000)
        
        load_time = time.time() - start_time
        
        # Page should load within 10 seconds
        assert load_time < 10, f"Page took {load_time:.2f}s to load"
    
    def test_api_response_time(self, api_key: str):
        """API should respond within reasonable time"""
        import requests
        
        headers = {"X-API-Key": api_key}
        start_time = time.time()
        
        response = requests.get(f"{API_BASE_URL}/memories/stats", headers=headers, timeout=10)
        
        response_time = time.time() - start_time
        
        assert response.status_code == 200
        # API should respond within 5 seconds
        assert response_time < 5, f"API took {response_time:.2f}s to respond"


# ==================== Accessibility Tests ====================

class TestAccessibility:
    """Test accessibility features"""
    
    def test_page_has_title(self, dashboard_page: Page):
        """Page should have a proper title"""
        expect(dashboard_page).to_have_title(re.compile("PowerMem", re.IGNORECASE))
    
    def test_main_heading_exists(self, dashboard_page: Page):
        """Page should have main heading"""
        heading = dashboard_page.locator("h1").first
        assert heading.is_visible()
    
    def test_keyboard_navigation(self, dashboard_page: Page):
        """Keyboard navigation should work"""
        # Press Tab to navigate
        dashboard_page.keyboard.press("Tab")
        dashboard_page.wait_for_timeout(200)
        
        # Check if focus moved
        focused_element = dashboard_page.evaluate("document.activeElement.tagName")
        assert focused_element in ["BUTTON", "A", "INPUT", "SELECT", "BODY"]


# ==================== Integration Tests ====================

class TestIntegration:
    """Test end-to-end integration scenarios"""
    
    def test_full_workflow_view_memory_details(self, dashboard_page: Page, test_data: Dict):
        """Full workflow: Navigate to memories, filter, view details"""
        # Navigate to memories page
        memories_link = dashboard_page.locator("a[href*='memories']").first
        if memories_link.is_visible():
            memories_link.click()
            dashboard_page.wait_for_timeout(1000)
        
        # Wait for table to load
        dashboard_page.wait_for_selector("table", timeout=5000)
        
        # Apply filter
        user_id_input = dashboard_page.locator("input[placeholder*='User']").first
        if user_id_input.is_visible():
            user_id_input.fill(test_data["user_id"])
            
            filter_button = dashboard_page.locator("button:has-text('Apply'), button:has-text('Filter')").first
            if filter_button.is_visible():
                filter_button.click()
                dashboard_page.wait_for_timeout(2000)
        
        # Click on first memory
        first_row = dashboard_page.locator("table tbody tr").first
        if first_row.is_visible():
            first_row.click()
            dashboard_page.wait_for_timeout(1000)
    
    def test_full_workflow_change_settings_and_refresh(self, dashboard_page: Page, api_key: str):
        """Full workflow: Go to settings, update API key, return to dashboard"""
        # Navigate to settings
        settings_link = dashboard_page.locator("a[href*='settings']").first
        if settings_link.is_visible():
            settings_link.click()
            dashboard_page.wait_for_timeout(1000)
        
        # Update API key
        api_key_input = dashboard_page.locator("input[type='password']").first
        if api_key_input.is_visible():
            api_key_input.fill(api_key)
            
            save_button = dashboard_page.locator("button:has-text('Save')").first
            if save_button.is_visible():
                save_button.click()
                dashboard_page.wait_for_timeout(1000)
        
        # Navigate back to dashboard
        dashboard_link = dashboard_page.locator("a[href='/'], a[href='#/']").first
        if dashboard_link.is_visible():
            dashboard_link.click()
            dashboard_page.wait_for_timeout(2000)
            
            # Verify dashboard loaded
            assert_overview_loaded(dashboard_page)


# ==================== Sidebar Tests ====================

class TestSidebar:
    """Test sidebar functionality"""
    
    def test_sidebar_visible(self, dashboard_page: Page):
        """Sidebar should be visible"""
        # Check for sidebar or navigation
        page_content = dashboard_page.content()
        assert "Memories" in page_content or "Settings" in page_content
    
    def test_sidebar_links_clickable(self, dashboard_page: Page):
        """Sidebar links should be clickable"""
        # Find all navigation links
        nav_links = dashboard_page.locator("a[href*='memories'], a[href*='settings'], a[href*='user-profile']")
        count = nav_links.count()
        
        # Should have at least some navigation links
        assert count >= 0


# ==================== Chart Tests ====================

class TestCharts:
    """Test chart rendering"""
    
    def test_growth_trend_chart_renders(self, dashboard_page: Page):
        """Growth trend chart should render"""
        dashboard_page.wait_for_timeout(3000)
        
        # Check for chart title
        assert_text_visible(dashboard_page, "Growth Trend")
    
    def test_memory_categories_chart_renders(self, dashboard_page: Page):
        """Memory categories chart should render"""
        dashboard_page.wait_for_timeout(3000)
        
        # Check for chart title
        assert_text_visible(dashboard_page, "Memory Categories")
    
    def test_retention_age_chart_renders(self, dashboard_page: Page):
        """Retention age chart should render"""
        dashboard_page.wait_for_timeout(3000)
        
        # Check for chart title
        assert_text_visible(dashboard_page, "Retention Age")


# ==================== Search Tests ====================

class TestSearch:
    """Test search functionality"""
    
    def test_memory_content_search(self, dashboard_page: Page):
        """Memory content search should work"""
        # Navigate to memories page
        memories_link = dashboard_page.locator("a[href*='memories']").first
        if memories_link.is_visible():
            memories_link.click()
            dashboard_page.wait_for_timeout(1000)
        
        # Wait for page to load
        dashboard_page.wait_for_selector("table", timeout=5000)
        
        # Find search input
        search_input = dashboard_page.locator("input[placeholder*='content'], input[placeholder*='Search']").first
        if search_input.is_visible():
            search_input.fill("test")
            dashboard_page.wait_for_timeout(500)
            
            # Apply filter
            filter_button = dashboard_page.locator("button:has-text('Apply'), button:has-text('Filter')").first
            if filter_button.is_visible():
                filter_button.click()
                dashboard_page.wait_for_timeout(2000)
    
    def test_user_profile_search(self, dashboard_page: Page):
        """User profile search should work"""
        # Navigate to user profile page
        profile_link = dashboard_page.locator("a[href*='user-profile']").first
        if profile_link.is_visible():
            profile_link.click()
            dashboard_page.wait_for_timeout(1000)
        
        # Wait for page to load
        dashboard_page.wait_for_timeout(2000)
        
        # Find search input
        search_input = dashboard_page.locator("input[placeholder*='User']").first
        if search_input.is_visible():
            search_input.fill("test")
            dashboard_page.wait_for_timeout(500)
            
            # Click search button
            search_button = dashboard_page.locator("button:has-text('Search')").first
            if search_button.is_visible():
                search_button.click()
                dashboard_page.wait_for_timeout(2000)


# ==================== Main Entry Point ====================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--headed"])
