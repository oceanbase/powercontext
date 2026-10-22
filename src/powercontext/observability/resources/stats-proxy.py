"""Internal read-only adapter from Grafana queries to the authorized statistics API.

Run only inside the observability network. Configuration and credentials are
mounted files; neither the request nor its query can select another upstream.
"""

from __future__ import annotations

import argparse
import hmac
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

MAX_RESPONSE_BYTES = 16 * 1024 * 1024


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # ty: ignore[missing-override-decorator] - standalone Python 3.11 stdlib.
        return None


class StatisticsProxy:
    def __init__(self, server_url: str, server_token: str, proxy_token: str):
        url = urlsplit(server_url)
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username
            or url.password
            or url.path not in {"", "/"}
            or url.query
            or url.fragment
        ):
            message = "Statistics upstream must be a fixed HTTP(S) origin"
            raise ValueError(message)
        if not proxy_token:
            message = "A statistics proxy credential is required"
            raise ValueError(message)
        self.server_url = server_url.rstrip("/")
        self.server_token = server_token
        self.proxy_token = proxy_token
        self.admission = threading.BoundedSemaphore(8)
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def query(self, scope_id: str, period: str, *, include_processing: bool = False) -> tuple[int, bytes]:
        if period not in {"today", "7d", "30d"} or len(scope_id) > 256 or (scope_id and not scope_id.strip()):
            return self.error(400, "invalid_statistics_selection")
        if not self.admission.acquire(blocking=False):
            return self.error(429, "statistics_proxy_busy")
        try:
            selection = {"mode": "exact", "scope_ids": [scope_id]} if scope_id else {"mode": "all"}
            headers = {"Content-Type": "application/json"}
            if self.server_token:
                headers["Authorization"] = "Bearer " + self.server_token
            request = Request(  # noqa: S310 - fixed validated HTTP(S) origin.
                self.server_url + "/v1/stats",
                data=json.dumps(
                    {
                        "selection": selection,
                        "period": period,
                        **({"include_processing": True} if include_processing else {}),
                    },
                    ensure_ascii=False,
                ).encode(),
                headers=headers,
                method="POST",
            )
            try:
                with self.opener.open(request, timeout=5) as response:
                    content = response.read(MAX_RESPONSE_BYTES + 1)
                if len(content) > MAX_RESPONSE_BYTES:
                    return self.error(502, "statistics_response_too_large")
                value = json.loads(content)
                # Deliberately expose statistics only; recurrence evidence and arbitrary new API fields are excluded.
                fields = ("scope_ids", "as_of", "inventory", "usage", "recall")
                result = {field: value[field] for field in fields}
                result["by_scope"] = [
                    {
                        **{field: row[field] for field in ("scope_id", "inventory", "usage", "recall")},
                        "bootstrap_delivery": (
                            None
                            if (delivery := row.get("bootstrap_delivery")) is None
                            else {
                                **{key: delivery.get(key) for key in ("period", "retention_days", "actual_adoption")},
                                "by_integration": [
                                    {
                                        key: item.get(key)
                                        for key in (
                                            "integration",
                                            "state",
                                            "receipts",
                                            "content_bytes",
                                            "last_prepared_at",
                                        )
                                    }
                                    for item in delivery.get("by_integration", [])
                                ],
                            }
                        ),
                        "processing": [
                            {
                                key: item.get(key)
                                for key in (
                                    "binding",
                                    "family",
                                    "state",
                                    "reason",
                                    "observed_at",
                                    "scheduler_observed",
                                    "requested_generation",
                                    "handled_generation",
                                    "dirty_generation",
                                    "clean_generation",
                                    "pending_since",
                                    "running_seconds",
                                    "next_check_seconds",
                                    "worker_id",
                                    "attempt_generation",
                                    "retry_failures",
                                    "block_code",
                                    "last_error_code",
                                    "last_error_stage",
                                    "last_error_at",
                                    "last_error_worker_id",
                                    "last_error_trace_id",
                                )
                            }
                            for item in row.get("processing", [])
                        ],
                        "experience_outcomes": {
                            key: row.get("recurrence", {}).get(key)
                            for key in ("selected", "recurred", "avoided", "unknown", "needing_review")
                        },
                    }
                    for row in value["by_scope"]
                ]
                body = json.dumps(result, ensure_ascii=False, allow_nan=False).encode()
            except HTTPError as error:
                return self.error(
                    error.code if error.code in {401, 403, 404, 422, 429} else 502, "statistics_upstream_rejected"
                )
            except (OSError, URLError, ValueError, KeyError, TypeError):
                return self.error(502, "statistics_upstream_unavailable")
            return 200, body
        finally:
            self.admission.release()

    @staticmethod
    def error(status: int, code: str) -> tuple[int, bytes]:
        return status, json.dumps({"error": code}).encode()


def handler(proxy: StatisticsProxy):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):  # noqa: A002  # ty: ignore[missing-override-decorator] - standalone Python 3.11 stdlib.
            # The base method uses this parameter name.

            pass

        def do_GET(self):
            supplied = self.headers.get("X-PC-Stats-Token", "")
            if not hmac.compare_digest(supplied.encode(), proxy.proxy_token.encode()):
                status, body = proxy.error(401, "statistics_proxy_unauthorized")
            else:
                parsed = urlsplit(self.path)
                query = parse_qs(parsed.query, keep_blank_values=True)
                if parsed.path != "/stats":
                    status, body = proxy.error(404, "statistics_route_not_found")
                elif any(
                    key not in {"scope_id", "period", "processing"} or len(values) != 1 for key, values in query.items()
                ) or query.get("processing", ["0"])[0] not in {"0", "1"}:
                    status, body = proxy.error(400, "invalid_statistics_selection")
                else:
                    status, body = proxy.query(
                        query.get("scope_id", [""])[0],
                        query.get("period", ["30d"])[0],
                        include_processing=query.get("processing", ["0"])[0] == "1",
                    )
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("/run/local/stats.json"))
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    proxy = StatisticsProxy(config["server_url"], config["server_token"], config["proxy_token"])
    server = ThreadingHTTPServer((args.bind, args.port), handler(proxy))
    server.daemon_threads = True
    server.serve_forever()


if __name__ == "__main__":
    main()
