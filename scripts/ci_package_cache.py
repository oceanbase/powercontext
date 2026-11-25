#!/usr/bin/env python3
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

"""Shared download bytes for fresh CI package installations, using only the standard library."""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import html
import json
import os
import re
import signal
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from html.parser import HTMLParser
from http.client import HTTPException
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, cast
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen

if TYPE_CHECKING:
    from typing_extensions import override
else:
    # Keep the deployed script dependency-free on Python 3.11 as well as 3.12+.
    def override(method):
        return method


DEFAULT_ORIGINS = {
    # A mirror's successful but stale index must not hide a newly published version.
    # Index metadata stays authoritative; large immutable downloads prefer the mirror.
    "pypi-simple": ("https://pypi.org/simple/", "https://mirrors.aliyun.com/pypi/simple/"),
    "pypi-packages": ("https://mirrors.aliyun.com/pypi/packages/", "https://files.pythonhosted.org/packages/"),
    "debian": ("https://mirrors.aliyun.com/debian/", "https://deb.debian.org/debian/"),
    "debian-security": ("https://mirrors.aliyun.com/debian-security/", "https://deb.debian.org/debian-security/"),
}


class OriginUnavailable(Exception):
    def __init__(self, status: int = 502):
        self.status = status
        super().__init__("Package origin unavailable")


@dataclass
class Resource:
    path: Path
    content_type: str
    immutable: bool
    digest: str


class IndexLinks(HTMLParser):
    """Change only download hrefs; preserve attributes, entities, text and metadata hints."""

    def __init__(self, rewrite: Callable[[str], str]):
        super().__init__(convert_charrefs=False)
        self.rewrite = rewrite
        self.parts: list[str] = []

    @override
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # HTMLParser has recorded the complete start tag before invoking this callback.
        raw = cast(str, self.get_starttag_text())
        if tag == "a":
            href = next((value for name, value in attrs if name == "href"), None)
            if href is not None:
                replacement = self.rewrite(href)
                if replacement != href:
                    raw = re.sub(
                        r"(?i)(\bhref\s*=\s*)(?:\"[^\"]*\"|'[^']*'|[^\s>]+)",
                        lambda match: match[1] + '"' + html.escape(replacement, quote=True) + '"',
                        raw,
                        count=1,
                    )
        self.parts.append(raw)

    @override
    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    @override
    def handle_endtag(self, tag: str) -> None:
        self.parts.append(f"</{tag}>")

    @override
    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    @override
    def handle_entityref(self, name: str) -> None:
        self.parts.append(f"&{name};")

    @override
    def handle_charref(self, name: str) -> None:
        self.parts.append(f"&#{name};")

    @override
    def handle_comment(self, data: str) -> None:
        self.parts.append(f"<!--{data}-->")

    @override
    def handle_decl(self, decl: str) -> None:
        self.parts.append(f"<!{decl}>")


class PackageCache:
    def __init__(
        self,
        directory: Path,
        origins: dict[str, tuple[str, str]] | None = None,
        *,
        timeout: float = 60,
        log: Callable[[dict[str, object]], None] | None = None,
    ):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.origins = origins or DEFAULT_ORIGINS
        self.timeout = timeout
        self.log = log

    def event(self, event: str, route: str, **values) -> None:
        # This standalone infrastructure service has no PowerContext runtime dependency.
        # Logging is best-effort and excludes request headers, queries and origin URLs.
        record = {"event": event, "route": urlsplit(route).path, **values}
        with contextlib.suppress(OSError, ValueError, TypeError):
            if self.log is not None:
                self.log(record)
            else:
                print(json.dumps(record, separators=(",", ":")), flush=True)

    @staticmethod
    def route(path: str) -> tuple[str, str, bool]:
        parsed = urlsplit(path)
        if parsed.scheme or parsed.netloc or "\x00" in unquote(parsed.path):
            message = "Invalid package path"
            raise ValueError(message)
        if any(part in {".", ".."} for part in unquote(parsed.path).split("/")):
            message = "Invalid package path"
            raise ValueError(message)
        for prefix, kind in (
            ("/pypi/simple/", "pypi-simple"),
            ("/pypi/packages/", "pypi-packages"),
            ("/debian/", "debian"),
            ("/debian-security/", "debian-security"),
        ):
            if parsed.path.startswith(prefix):
                suffix = parsed.path[len(prefix) :]
                immutable = kind == "pypi-packages" or (
                    kind.startswith("debian") and (suffix.startswith("pool/") or "/by-hash/" in suffix)
                )
                return kind, suffix + ("?" + parsed.query if parsed.query else ""), immutable
        message = "Unknown package route"
        raise ValueError(message)

    def rewrite_link(self, href: str, origin_url: str) -> str:
        absolute = urlsplit(urljoin(origin_url, href))
        for kind, prefix in (("pypi-packages", "/pypi/packages/"), ("pypi-simple", "/pypi/simple/")):
            for base in (*self.origins[kind], *DEFAULT_ORIGINS[kind]):
                parsed = urlsplit(base)
                if absolute.netloc == parsed.netloc and absolute.path.startswith(parsed.path):
                    return urlunsplit((
                        "",
                        "",
                        prefix + absolute.path[len(parsed.path) :],
                        absolute.query,
                        absolute.fragment,
                    ))
        return href

    def download(self, route: str, kind: str, suffix: str, target: Path) -> Resource:
        statuses = []
        for position, base in enumerate(self.origins[kind]):
            source = "primary" if position == 0 else "fallback"
            started = time.monotonic()
            if position:
                self.event("fallback", route, source=source)
            try:
                request = Request(  # noqa: S310 - deployment-owned HTTP(S) package origins
                    urljoin(base, suffix.lstrip("/")),
                    headers={"Accept": "text/html" if kind == "pypi-simple" else "*/*", "Accept-Encoding": "identity"},
                )
                with urlopen(request, timeout=self.timeout) as response, target.open("wb") as output:  # noqa: S310
                    if response.status != 200:
                        message = "Incomplete origin response"
                        raise OSError(message)  # noqa: TRY301 - both origins share this transfer validation
                    expected = response.headers.get("Content-Length")
                    size = 0
                    digest = hashlib.sha256()
                    while chunk := response.read(1024 * 1024):
                        output.write(chunk)
                        digest.update(chunk)
                        size += len(chunk)
                    if expected is not None and size != int(expected):
                        message = "Truncated origin response"
                        raise OSError(message)  # noqa: TRY301 - both origins share this transfer validation
                    content_type = response.headers.get("Content-Type", "application/octet-stream")
                    final_url = response.url
                    output.flush()
                    os.fsync(output.fileno())
                if kind == "pypi-simple":
                    parser = IndexLinks(lambda href, base=final_url: self.rewrite_link(href, base))
                    parser.feed(target.read_text(encoding="utf-8"))
                    parser.close()
                    body = "".join(parser.parts).encode()
                    target.write_bytes(body)
                    digest = hashlib.sha256(body)
                    content_type = "text/html; charset=utf-8"
                self.event(
                    "origin_fetch",
                    route,
                    object_key=hashlib.sha256((kind + ":" + suffix).encode()).hexdigest(),
                    source=source,
                    bytes=size,
                    duration_ms=round((time.monotonic() - started) * 1000),
                )
                return Resource(target, content_type, False, digest.hexdigest())
            except (OSError, URLError, HTTPException, ValueError) as error:
                statuses.append(error.code if isinstance(error, HTTPError) else 502)
                self.event("origin_error", route, source=source, error_category=type(error).__name__)
                target.unlink(missing_ok=True)
        raise OriginUnavailable(404 if statuses and all(status == 404 for status in statuses) else 502)

    def cached(self, route: str, key: str, target: Path, metadata: Path) -> Resource | None:
        try:
            info = json.loads(metadata.read_text())
            if target.stat().st_size == info["bytes"]:
                self.event("hit", route, object_key=key, bytes=info["bytes"])
                return Resource(target, info["content_type"], True, info["sha256"])
        except (OSError, ValueError, KeyError):
            pass
        return None

    def get(self, route: str) -> Resource:
        kind, suffix, immutable = self.route(route)
        key = hashlib.sha256((kind + ":" + suffix).encode()).hexdigest()
        started = time.time_ns()
        target = self.directory / (key + ".body")
        metadata = self.directory / (key + ".json")
        failure = self.directory / (key + ".failure")

        if immutable and (resource := self.cached(route, key, target, metadata)) is not None:
            return resource
        if not immutable:
            fd, name = tempfile.mkstemp(prefix="index-", dir=self.directory)
            os.close(fd)
            return self.download(route, kind, suffix, Path(name))
        with (self.directory / (key + ".lock")).open("a+b") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                self.event("wait", route, object_key=key)
                fcntl.flock(lock, fcntl.LOCK_EX)
            if (resource := self.cached(route, key, target, metadata)) is not None:
                return resource
            with contextlib.suppress(OSError, ValueError):
                failed = json.loads(failure.read_text())
                if failed["at"] >= started:
                    raise OriginUnavailable(failed["status"])
            fd, name = tempfile.mkstemp(prefix=key + "-", dir=self.directory)
            os.close(fd)
            temporary = Path(name)
            try:
                resource = self.download(route, kind, suffix, temporary)
                os.replace(temporary, target)
                info = {
                    "content_type": resource.content_type,
                    "sha256": resource.digest,
                    "bytes": target.stat().st_size,
                }
                temporary.write_text(json.dumps(info))
                os.replace(temporary, metadata)
                failure.unlink(missing_ok=True)
                return Resource(target, resource.content_type, True, resource.digest)
            except OriginUnavailable as error:
                temporary.write_text(json.dumps({"at": time.time_ns(), "status": error.status}))
                os.replace(temporary, failure)
                raise
            finally:
                temporary.unlink(missing_ok=True)


class PackageCacheServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = False

    def __init__(self, address: tuple[str, int], cache: PackageCache):
        self.cache = cache
        super().__init__(address, PackageCacheHandler)


class PackageCacheHandler(BaseHTTPRequestHandler):
    server: PackageCacheServer

    @override
    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - match the base method's keyword argument
        pass

    def do_HEAD(self) -> None:
        self.do_GET()

    @staticmethod
    def byte_range(value: str, size: int) -> tuple[int, int] | None:
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", value)
        if not match or not any(match.groups()):
            return None
        left, right = match.groups()
        start = int(left) if left else max(0, size - int(right))
        end = min(size - 1, int(right)) if left and right else size - 1
        return (start, end) if start <= end and start < size else None

    def do_GET(self) -> None:
        resource = None
        try:
            if self.path == "/healthz":
                self.respond(200, b"ok\n")
                return
            resource = self.server.cache.get(self.path)
            self.serve_resource(resource)
        except OriginUnavailable as error:
            self.respond(error.status, b"Package origin unavailable\n", {"X-Package-Cache-Error": "origin_unavailable"})
        except ValueError:
            self.respond(404, b"Unknown package route\n")
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            if resource is not None and not resource.immutable:
                resource.path.unlink(missing_ok=True)

    def serve_resource(self, resource: Resource) -> None:
        size = resource.path.stat().st_size
        start, end = 0, size - 1
        ranged = self.headers.get("Range") if self.command == "GET" else None
        etag = '"' + resource.digest + '"'
        if self.headers.get("If-Range", etag) != etag:
            ranged = None
        if ranged:
            selected = self.byte_range(ranged, size)
            if selected is None:
                self.respond(416, b"Unsatisfiable byte range\n", {"Content-Range": f"bytes */{size}"})
                return
            start, end = selected
        self.send_response(206 if ranged else 200)
        self.send_header("Content-Length", str(max(0, end - start + 1)))
        self.send_header("Content-Type", resource.content_type)
        self.send_header("ETag", etag)
        self.send_header("Cache-Control", "public, max-age=31536000, immutable" if resource.immutable else "no-cache")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Connection", "close")
        if ranged:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if self.command != "HEAD":
            with resource.path.open("rb") as source:
                source.seek(start)
                remaining = end - start + 1
                while remaining > 0 and (chunk := source.read(min(1024 * 1024, remaining))):
                    self.wfile.write(chunk)
                    remaining -= len(chunk)

    def respond(self, status: int, body: bytes, headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")  # noqa: S104 - an explicitly deployed internal HTTP service
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=60, help="Origin socket timeout in seconds")
    for kind, urls in DEFAULT_ORIGINS.items():
        for source, default in zip(("primary", "fallback"), urls, strict=True):
            parser.add_argument(f"--{kind}-{source}", default=default)
    args = parser.parse_args()
    origins = {
        kind: tuple(
            getattr(args, (kind + "-" + source).replace("-", "_")).rstrip("/") + "/"
            for source in ("primary", "fallback")
        )
        for kind in DEFAULT_ORIGINS
    }
    server = PackageCacheServer((args.host, args.port), PackageCache(args.cache_dir, origins, timeout=args.timeout))
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: threading.Thread(target=server.shutdown, daemon=True).start())
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
