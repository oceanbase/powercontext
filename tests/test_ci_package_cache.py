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

from __future__ import annotations

import importlib.util
import sys
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "ci_package_cache.py"


@pytest.fixture
def service_module():
    assert SCRIPT.is_file(), "The shared package cache service has not been implemented"
    spec = importlib.util.spec_from_file_location("ci_package_cache", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@contextmanager
def serving(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


class Origin:
    def __init__(self):
        self.counts = Counter()
        self.responses = {}
        self.headers = {}

    def server(self):
        origin = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                origin.counts[self.path] += 1
                origin.headers[self.path] = dict(self.headers)
                response = origin.responses.get(self.path, (404, b"missing", {}))
                if callable(response):
                    response = response()
                status, body, headers = response
                headers = dict(headers)
                self.send_response(status)
                self.send_header("Content-Length", str(headers.pop("length", len(body))))
                for name, value in headers.items():
                    self.send_header(name, value)
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - preserve the base method's signature
                pass

        return ThreadingHTTPServer(("127.0.0.1", 0), Handler)


def get(url, *, method="GET", headers=None):
    try:
        with urlopen(Request(url, method=method, headers=headers or {}), timeout=10) as response:  # noqa: S310 - local HTTP fixtures
            return response.status, response.read(), response.headers
    except HTTPError as error:
        return error.code, error.read(), error.headers


def make_service(module, path, upstream, events=None):
    origins = {
        "pypi-simple": (upstream + "/primary/simple/", upstream + "/fallback/simple/"),
        "pypi-packages": (upstream + "/primary/packages/", upstream + "/fallback/packages/"),
        "debian": (upstream + "/primary/debian/", upstream + "/fallback/debian/"),
        "debian-security": (upstream + "/primary/security/", upstream + "/fallback/security/"),
    }
    cache = module.PackageCache(path, origins, timeout=3, log=(events.append if events is not None else None))
    return module.PackageCacheServer(("127.0.0.1", 0), cache)


@pytest.mark.parametrize("prefix", ["/pypi/simple/", "/pypi/packages/", "/debian/", "/debian-security/"])
def test_invalid_repository_paths_are_rejected_before_fetch_or_cache_write(
    service_module, tmp_path, monkeypatch, prefix
):
    requests = []

    def no_network(request, **kwargs):
        requests.append(request.full_url)
        message = "This validation test must not access an upstream"
        raise OSError(message)

    monkeypatch.setattr(service_module, "urlopen", no_network)
    with serving(make_service(service_module, tmp_path, "https://repository.invalid")) as endpoint:
        for suffix in (
            "urn:artifact",
            "https://repository.invalid/artifact.whl",
            "//repository.invalid/artifact.whl",
            "/artifact.whl",
            "%2e%2e/artifact.whl",
        ):
            for method in ("GET", "HEAD"):
                assert get(endpoint + prefix + suffix, method=method)[0] == 404
    assert requests == []
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("route", "expected"),
    [
        ("/pypi/simple/", "/primary/simple/"),
        ("/pypi/packages/aa/pkg%2Bbuild.whl?download=1", "/primary/packages/aa/pkg%2Bbuild.whl?download=1"),
        ("/debian/pool/p/pkg_1%3a2_amd64.deb", "/primary/debian/pool/p/pkg_1%3a2_amd64.deb"),
        ("/debian-security/dists/bookworm/InRelease", "/primary/security/dists/bookworm/InRelease"),
    ],
)
def test_repository_paths_and_queries_keep_the_configured_origin(service_module, tmp_path, route, expected):
    origin = Origin()
    origin.responses[expected] = 200, b"repository content", {}
    with serving(origin.server()) as upstream, serving(make_service(service_module, tmp_path, upstream)) as endpoint:
        assert get(endpoint + route)[:2] == (200, b"repository content")
    assert origin.counts == {expected: 1}


def test_concurrent_cold_requests_and_restarted_service_reuse_one_object(service_module, tmp_path):
    origin = Origin()
    started, release = threading.Event(), threading.Event()

    def delayed():
        started.set()
        assert release.wait(5)
        return 200, b"wheel bytes", {}

    origin.responses["/primary/packages/aa/pkg.whl"] = delayed
    events = []
    with serving(origin.server()) as upstream:
        with (
            serving(make_service(service_module, tmp_path, upstream, events)) as endpoint,
            ThreadPoolExecutor(max_workers=8) as pool,
        ):
            futures = [pool.submit(get, endpoint + "/pypi/packages/aa/pkg.whl") for _ in range(8)]
            assert started.wait(5)
            release.set()
            assert [(f.result()[0], f.result()[1]) for f in futures] == [(200, b"wheel bytes")] * 8
        with serving(make_service(service_module, tmp_path, upstream)) as restarted:
            assert get(restarted + "/pypi/packages/aa/pkg.whl")[:2] == (200, b"wheel bytes")
    assert origin.counts["/primary/packages/aa/pkg.whl"] == 1
    assert any(e["event"] == "origin_fetch" for e in events)
    assert any(e["event"] == "hit" for e in events)


def test_different_objects_do_not_wait_for_an_unrelated_fill(service_module, tmp_path):
    origin = Origin()
    started, release = threading.Event(), threading.Event()

    def delayed():
        started.set()
        assert release.wait(5)
        return 200, b"slow", {}

    origin.responses["/primary/packages/slow.whl"] = delayed
    origin.responses["/primary/packages/fast.whl"] = 200, b"fast", {}
    with (
        serving(origin.server()) as upstream,
        serving(make_service(service_module, tmp_path, upstream)) as endpoint,
        ThreadPoolExecutor(max_workers=2) as pool,
    ):
        slow = pool.submit(get, endpoint + "/pypi/packages/slow.whl")
        assert started.wait(5)
        try:
            assert pool.submit(get, endpoint + "/pypi/packages/fast.whl").result(timeout=2)[:2] == (200, b"fast")
        finally:
            release.set()
        assert slow.result()[:2] == (200, b"slow")


def test_head_and_ranges_share_complete_cached_file(service_module, tmp_path):
    origin = Origin()
    origin.responses["/primary/packages/pkg.whl"] = 200, b"0123456789", {}
    with serving(origin.server()) as upstream, serving(make_service(service_module, tmp_path, upstream)) as endpoint:
        url = endpoint + "/pypi/packages/pkg.whl"
        status, body, headers = get(url, method="HEAD")
        assert (status, body, headers["Content-Length"]) == (200, b"", "10")
        assert get(url, headers={"Range": "bytes=2-5"})[:2] == (206, b"2345")
        assert get(url, headers={"Range": "bytes=-3"})[:2] == (206, b"789")
        assert get(url, headers={"Range": "bytes=99-"})[0] == 416
        assert get(url, headers={"Range": "bytes=2-5", "If-Range": '"old-object"'})[:2] == (200, b"0123456789")
        assert get(url, headers={"Range": "bytes=2-5", "If-Range": headers["ETag"]})[:2] == (206, b"2345")
        assert get(url)[:2] == (200, b"0123456789")
    assert origin.counts["/primary/packages/pkg.whl"] == 1


def test_simple_index_refreshes_and_rewrites_links_without_losing_hashes_or_attributes(service_module, tmp_path):
    origin = Origin()
    with serving(origin.server()) as upstream:
        page = (
            '<!DOCTYPE html><a href="../../packages/aa/pkg.whl#sha256=abc" '
            'data-requires-python="&gt;=3.11" data-yanked="broken" data-core-metadata="sha256=def">pkg.whl</a>'
            f'<a href="{upstream}/fallback/packages/bb/pkg.tar.gz#sha256=ghi">pkg.tar.gz</a>'
        ).encode()
        origin.responses["/primary/simple/pkg/"] = 200, page, {"Content-Type": "text/html"}
        origin.responses["/primary/packages/aa/pkg.whl.metadata"] = 200, b"Name: pkg\n", {}
        with serving(make_service(service_module, tmp_path, upstream)) as endpoint:
            status, body, headers = get(endpoint + "/pypi/simple/pkg/")
            assert status == 200
            assert b'href="/pypi/packages/aa/pkg.whl#sha256=abc"' in body
            assert b'href="/pypi/packages/bb/pkg.tar.gz#sha256=ghi"' in body
            assert b'data-requires-python="&gt;=3.11"' in body
            assert b'data-yanked="broken"' in body
            assert b'data-core-metadata="sha256=def"' in body
            assert "no-cache" in headers["Cache-Control"]
            assert get(endpoint + "/pypi/packages/aa/pkg.whl.metadata")[:2] == (200, b"Name: pkg\n")
            origin.responses["/primary/simple/pkg/"] = 200, b"<html>new version</html>", {"Content-Type": "text/html"}
            assert get(endpoint + "/pypi/simple/pkg/")[1] == b"<html>new version</html>"
    assert origin.counts["/primary/simple/pkg/"] == 2
    assert "text/html" in origin.headers["/primary/simple/pkg/"]["Accept"]


def test_truncated_primary_falls_back_once_and_never_publishes_partial_bytes(service_module, tmp_path):
    origin = Origin()
    origin.responses["/primary/packages/pkg.whl"] = 200, b"short", {"length": 100}
    origin.responses["/fallback/packages/pkg.whl"] = 200, b"complete wheel", {}
    events = []
    with (
        serving(origin.server()) as upstream,
        serving(make_service(service_module, tmp_path, upstream, events)) as endpoint,
    ):
        assert get(endpoint + "/pypi/packages/pkg.whl")[:2] == (200, b"complete wheel")
        assert get(endpoint + "/pypi/packages/pkg.whl")[:2] == (200, b"complete wheel")
    assert origin.counts == {"/primary/packages/pkg.whl": 1, "/fallback/packages/pkg.whl": 1}
    assert any(e["event"] == "fallback" for e in events)


def test_apt_signed_metadata_is_unchanged_and_refreshed_while_packages_are_reused(service_module, tmp_path):
    origin = Origin()
    release = b"-----BEGIN PGP SIGNED MESSAGE-----\r\nHash: SHA256\r\n"
    origin.responses["/primary/debian/dists/bookworm/InRelease"] = 200, release, {}
    origin.responses["/primary/debian/pool/main/p/pkg_1_amd64.deb"] = 200, b"deb bytes", {}
    origin.responses["/primary/debian/dists/bookworm/main/by-hash/SHA256/abc"] = 200, b"index bytes", {}
    with serving(origin.server()) as upstream, serving(make_service(service_module, tmp_path, upstream)) as endpoint:
        for _ in range(2):
            assert get(endpoint + "/debian/dists/bookworm/InRelease")[:2] == (200, release)
            assert get(endpoint + "/debian/pool/main/p/pkg_1_amd64.deb")[:2] == (200, b"deb bytes")
            assert get(endpoint + "/debian/dists/bookworm/main/by-hash/SHA256/abc")[:2] == (200, b"index bytes")
    assert origin.counts["/primary/debian/dists/bookworm/InRelease"] == 2
    assert origin.counts["/primary/debian/pool/main/p/pkg_1_amd64.deb"] == 1
    assert origin.counts["/primary/debian/dists/bookworm/main/by-hash/SHA256/abc"] == 1


def test_current_waiters_share_failed_fill_and_later_request_can_retry(service_module, tmp_path):
    origin = Origin()
    started, release, waiting = threading.Event(), threading.Event(), threading.Event()
    waits = []

    def observe(event):
        if event["event"] == "wait":
            waits.append(event)
            if len(waits) >= 3:
                waiting.set()

    def delayed_failure():
        started.set()
        assert release.wait(5)
        return 503, b"unavailable", {}

    origin.responses["/primary/packages/pkg.whl"] = delayed_failure
    origin.responses["/fallback/packages/pkg.whl"] = 503, b"unavailable", {}
    with serving(origin.server()) as upstream:
        server = make_service(service_module, tmp_path, upstream)
        server.cache.log = observe
        with serving(server) as endpoint, ThreadPoolExecutor(max_workers=4) as pool:
            first = pool.submit(get, endpoint + "/pypi/packages/pkg.whl")
            assert started.wait(5)
            rest = [pool.submit(get, endpoint + "/pypi/packages/pkg.whl") for _ in range(3)]
            try:
                assert waiting.wait(5)
            finally:
                release.set()
            results = [f.result() for f in [first, *rest]]
            assert [r[0] for r in results] == [502] * 4
            assert all(r[2]["X-Package-Cache-Error"] == "origin_unavailable" for r in results)
            assert origin.counts == {"/primary/packages/pkg.whl": 1, "/fallback/packages/pkg.whl": 1}
            origin.responses["/primary/packages/pkg.whl"] = 200, b"recovered", {}
            assert get(endpoint + "/pypi/packages/pkg.whl")[:2] == (200, b"recovered")


def test_origin_redirect_is_filled_once_and_metadata_404_is_not_permanent(service_module, tmp_path):
    origin = Origin()
    origin.responses["/primary/packages/pkg.whl"] = 302, b"", {"Location": "/cdn/pkg.whl"}
    origin.responses["/cdn/pkg.whl"] = 200, b"redirected wheel", {}
    with serving(origin.server()) as upstream, serving(make_service(service_module, tmp_path, upstream)) as endpoint:
        assert get(endpoint + "/pypi/packages/pkg.whl")[:2] == (200, b"redirected wheel")
        assert get(endpoint + "/pypi/packages/pkg.whl")[:2] == (200, b"redirected wheel")
        assert get(endpoint + "/pypi/simple/new/")[0] == 404
        origin.responses["/primary/simple/new/"] = 200, b"<html>published</html>", {}
        assert get(endpoint + "/pypi/simple/new/")[:2] == (200, b"<html>published</html>")
    assert origin.counts["/primary/packages/pkg.whl"] == 1
    assert origin.counts["/cdn/pkg.whl"] == 1


def test_graceful_server_close_waits_for_active_fill(service_module, tmp_path):
    origin = Origin()
    started, release, closed = threading.Event(), threading.Event(), threading.Event()

    def delayed():
        started.set()
        assert release.wait(5)
        return 200, b"complete before shutdown", {}

    origin.responses["/primary/packages/pkg.whl"] = delayed
    with serving(origin.server()) as upstream, ThreadPoolExecutor() as executor:
        server = make_service(service_module, tmp_path, upstream)
        serving_thread = threading.Thread(target=server.serve_forever, daemon=True)
        serving_thread.start()
        request = executor.submit(get, f"http://127.0.0.1:{server.server_port}/pypi/packages/pkg.whl")
        assert started.wait(5)
        server.shutdown()

        def close():
            server.server_close()
            closed.set()

        closer = threading.Thread(target=close)
        closer.start()
        try:
            assert not closed.wait(0.1), "Graceful shutdown must drain an in-flight object fill"
        finally:
            release.set()
            closer.join(5)
            serving_thread.join(5)
        assert request.result()[:2] == (200, b"complete before shutdown")
        assert closed.is_set()


def test_interrupted_failure_record_does_not_block_a_later_fill(service_module, tmp_path):
    origin = Origin()
    origin.responses["/primary/packages/pkg.whl"] = 503, b"unavailable", {}
    origin.responses["/fallback/packages/pkg.whl"] = 503, b"unavailable", {}
    with serving(origin.server()) as upstream, serving(make_service(service_module, tmp_path, upstream)) as endpoint:
        url = endpoint + "/pypi/packages/pkg.whl"
        assert get(url)[0] == 502
        # Simulate an old service being killed while writing the failure record.
        next(tmp_path.glob("*.failure")).write_bytes(b'{"at":')
        origin.responses["/primary/packages/pkg.whl"] = 200, b"recovered", {}
        assert get(url)[:2] == (200, b"recovered")


def test_new_release_uses_authoritative_index_and_mirror_package_bytes(service_module, tmp_path):
    origin = Origin()
    origin.responses["/mirror/simple/pkg/"] = 200, b'<a href="../../packages/pkg-1.whl">old</a>', {}
    origin.responses["/official/simple/pkg/"] = (
        200,
        b'<a href="https://files.pythonhosted.org/packages/pkg-2.whl#sha256=abc" data-requires-python=">=3.11">new</a>',
        {},
    )
    origin.responses["/mirror/packages/pkg-2.whl"] = 200, b"new wheel bytes", {}
    with serving(origin.server()) as upstream:
        locations = {
            "https://mirrors.aliyun.com/pypi/simple/": upstream + "/mirror/simple/",
            "https://pypi.org/simple/": upstream + "/official/simple/",
            "https://mirrors.aliyun.com/pypi/packages/": upstream + "/mirror/packages/",
            "https://files.pythonhosted.org/packages/": upstream + "/official/packages/",
        }
        origins = {
            kind: tuple(locations.get(url, url) for url in urls)
            for kind, urls in service_module.DEFAULT_ORIGINS.items()
        }
        cache = service_module.PackageCache(tmp_path, origins)
        server = service_module.PackageCacheServer(("127.0.0.1", 0), cache)
        with serving(server) as endpoint:
            status, body, _ = get(endpoint + "/pypi/simple/pkg/")
            assert status == 200
            assert b"/pypi/packages/pkg-2.whl#sha256=abc" in body
            assert b'data-requires-python=">=3.11"' in body
            assert get(endpoint + "/pypi/packages/pkg-2.whl")[:2] == (200, b"new wheel bytes")
    assert origin.counts == {"/official/simple/pkg/": 1, "/mirror/packages/pkg-2.whl": 1}


@pytest.mark.parametrize(
    ("validator", "conditional", "initial", "changed"),
    [
        ("ETag", "If-None-Match", '"release-1"', '"release-2"'),
        ("Last-Modified", "If-Modified-Since", "Mon, 23 Nov 2026 00:00:00 GMT", "Tue, 24 Nov 2026 00:00:00 GMT"),
    ],
)
def test_index_revalidation_survives_restart_and_new_release_refreshes(
    service_module, tmp_path, validator, conditional, initial, changed
):
    origin = Origin()
    current = [initial, b"<html>release one</html>"]
    transfers = []

    def index():
        if origin.headers["/primary/simple/pkg/"].get(conditional) == current[0]:
            transfers.append(0)
            return 304, b"", {validator: current[0]}
        transfers.append(len(current[1]))
        return 200, current[1], {validator: current[0]}

    origin.responses["/primary/simple/pkg/"] = index
    with serving(origin.server()) as upstream:
        with serving(make_service(service_module, tmp_path, upstream)) as endpoint:
            assert get(endpoint + "/pypi/simple/pkg/")[:2] == (200, current[1])
        with serving(make_service(service_module, tmp_path, upstream)) as endpoint:
            assert get(endpoint + "/pypi/simple/pkg/")[:2] == (200, current[1])
            assert transfers == [len(current[1]), 0]
            current[:] = changed, b"<html>newly published release</html>"
            assert get(endpoint + "/pypi/simple/pkg/")[:2] == (200, current[1])
            assert transfers[-1] == len(current[1])


def test_concurrent_index_requests_share_one_validation_but_later_request_revalidates(service_module, tmp_path):
    origin = Origin()
    path = "/primary/simple/pkg/"
    body = b"<html>release one</html>"
    origin.responses[path] = 200, body, {"ETag": '"one"'}
    started, release, waiting = threading.Event(), threading.Event(), threading.Event()
    waits = []

    def observe(event):
        if event["event"] == "wait":
            waits.append(event)
            if len(waits) == 3:
                waiting.set()

    def validate():
        started.set()
        assert release.wait(5)
        assert origin.headers[path].get("If-None-Match") == '"one"'
        return 304, b"", {}

    with serving(origin.server()) as upstream:
        server = make_service(service_module, tmp_path, upstream)
        server.cache.log = observe
        with serving(server) as endpoint, ThreadPoolExecutor(max_workers=4) as pool:
            url = endpoint + "/pypi/simple/pkg/"
            assert get(url)[:2] == (200, body)
            origin.responses[path] = validate
            first = pool.submit(get, url)
            assert started.wait(5)
            rest = [pool.submit(get, url) for _ in range(3)]
            try:
                assert waiting.wait(5)
            finally:
                release.set()
            assert [result.result()[:2] for result in [first, *rest]] == [(200, body)] * 4
            assert origin.counts[path] == 2
            assert get(url)[:2] == (200, body)
            assert origin.counts[path] == 3


def test_index_fallback_validators_never_cross_origins(service_module, tmp_path):
    origin = Origin()
    primary, fallback = "/primary/simple/pkg/", "/fallback/simple/pkg/"
    origin.responses[primary] = 503, b"unavailable", {}
    origin.responses[fallback] = 200, b"<html>mirror</html>", {"ETag": '"mirror"'}
    with serving(origin.server()) as upstream, serving(make_service(service_module, tmp_path, upstream)) as endpoint:
        url = endpoint + "/pypi/simple/pkg/"
        assert get(url)[:2] == (200, b"<html>mirror</html>")
        origin.responses[primary] = 200, b"<html>official</html>", {"ETag": '"official"'}
        assert get(url)[:2] == (200, b"<html>official</html>")
        assert "If-None-Match" not in origin.headers[primary]
        origin.responses[primary] = 503, b"unavailable", {}
        assert get(url)[:2] == (200, b"<html>mirror</html>")
        assert origin.headers[primary].get("If-None-Match") == '"official"'
        assert "If-None-Match" not in origin.headers[fallback]
        origin.responses[fallback] = 304, b"", {}
        assert get(url)[:2] == (200, b"<html>mirror</html>")
        assert "If-None-Match" not in origin.headers[primary]
        assert origin.headers[fallback].get("If-None-Match") == '"mirror"'


def test_failed_index_validation_preserves_last_good_validators_without_serving_stale_data(service_module, tmp_path):
    origin = Origin()
    primary, fallback = "/primary/simple/pkg/", "/fallback/simple/pkg/"
    body = b"<html>known release</html>"
    origin.responses[primary] = 200, body, {"ETag": '"known"'}
    with serving(origin.server()) as upstream, serving(make_service(service_module, tmp_path, upstream)) as endpoint:
        url = endpoint + "/pypi/simple/pkg/"
        assert get(url)[:2] == (200, body)
        origin.responses[primary] = 503, b"unavailable", {}
        origin.responses[fallback] = 503, b"unavailable", {}
        assert get(url)[0] == 502
        origin.responses[primary] = 304, b"", {}
        assert get(url)[:2] == (200, body)
        assert origin.headers[primary].get("If-None-Match") == '"known"'


def test_index_snapshot_remains_stable_during_a_later_refresh(service_module, tmp_path):
    origin = Origin()
    path = "/primary/simple/pkg/"
    origin.responses[path] = 200, b"<html>old release</html>", {"ETag": '"old"'}
    with serving(origin.server()) as upstream, make_service(service_module, tmp_path, upstream) as server:
        cache = server.cache
        first = cache.get("/pypi/simple/pkg/")
        origin.responses[path] = 200, b"<html>new release with more bytes</html>", {"ETag": '"new"'}
        second = cache.get("/pypi/simple/pkg/")
        try:
            assert first.path.read_bytes() == b"<html>old release</html>"
            assert second.path.read_bytes() == b"<html>new release with more bytes</html>"
        finally:
            first.path.unlink()
            second.path.unlink()
