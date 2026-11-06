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

"""Query real storage and verify correlation. Requires an explicitly started isolated demo."""

from __future__ import annotations

import argparse
import json
import re
import secrets
import time
from pathlib import Path

import httpx


def require(condition: object, message: str = "Acceptance check failed") -> None:
    if not condition:
        raise RuntimeError(message)


def wait_for(check, *, timeout: int = 60):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            last = check()
            if last:
                return last
        except (httpx.HTTPError, KeyError, ValueError):
            pass
        time.sleep(1)
    message = f"Condition did not pass within {timeout} seconds; last result: {last!r}"
    raise RuntimeError(message)


def verify_background(
    server: httpx.Client, backend: httpx.Client, background_url: str, exercise: bool
) -> list[dict[str, str]]:
    report: list[dict[str, str]] = []
    with httpx.Client(base_url=background_url, headers=server.headers, trust_env=False, timeout=10) as background:
        require(httpx.get(background_url + "/metrics", trust_env=False).status_code == 401)
        require(background.get("/metrics").status_code == 200)
        require(background.get("/health/live").status_code == 200)
        require(background.get("/v1/capabilities").status_code == 404)
        wait_for(
            lambda: any(
                item["value"][1] == "1"
                for item in backend.get(
                    "/prometheus/api/v1/query",
                    params={
                        "query": 'up{job="powercontext",role="background"}',
                    },
                ).json()["data"]["result"]
            )
        )
        report.append({
            "check": "independent authenticated background scrape with no business routes",
            "result": "pass",
        })
        if exercise:
            begin = time.time()
            expression = 'powercontext_server_artifact_processing_completed_total{role="background",family="memory"}'
            before = sum(
                float(row["value"][1])
                for row in backend.get("/prometheus/api/v1/query", params={"query": expression}).json()["data"][
                    "result"
                ]
            )
            scope = server.get("/v1/scopes/default").json()["scope_id"]
            response = server.post(
                "/v1/sources/content",
                json={
                    "scope_id": scope,
                    "source_id": "reference-" + secrets.token_hex(8),
                    "content": "Explicit isolated reference background acceptance.",
                },
            )
            require(response.status_code == 202)
            wait_for(
                lambda: (
                    sum(
                        float(row["value"][1])
                        for row in backend.get("/prometheus/api/v1/query", params={"query": expression}).json()["data"][
                            "result"
                        ]
                    )
                    > before
                ),
                timeout=90,
            )
            worker_logs = wait_for(
                lambda: backend.get(
                    "/loki/loki/api/v1/query_range",
                    params={
                        "query": '{service_name="powercontext-server"} | json | service_role="background" | event="artifact_processing.worker.acknowledged"',
                        "start": str(int(begin * 1_000_000_000)),
                    },
                ).json()["data"]["result"]
            )
            worker_trace_ids = [
                json.loads(body).get("trace_id") for stream in worker_logs for _timestamp, body in stream["values"]
            ]
            require(worker_trace_ids and worker_trace_ids[0])
            worker_trace_id = worker_trace_ids[0]
            wait_for(lambda: backend.get("/tempo/api/traces/" + worker_trace_id).status_code == 200)
            stored = backend.get("/tempo/api/traces/" + worker_trace_id, headers={"Accept": "application/json"})
            require("artifact_processing.worker.execute" in stored.text)
            require("chat " in stored.text)
            require("Explicit isolated reference background acceptance." not in stored.text)
            report.append({
                "check": "standalone background acknowledgement, matching worker/model trace and log",
                "result": "pass",
            })
    return report


def verify_provisioning(grafana: httpx.Client, backend: httpx.Client) -> None:
    for uid in ("prometheus", "tempo", "loki", "pc-stats", "pc-alerts"):
        response = grafana.get("/api/datasources/uid/" + uid)
        response.raise_for_status()
        require(response.json()["uid"] == uid)
    for uid in ("pc-service", "pc-processing", "pc-dependencies", "pc-business", "pc-scopes"):
        dashboard = grafana.get("/api/dashboards/uid/" + uid)
        dashboard.raise_for_status()
        for panel in dashboard.json()["dashboard"]["panels"]:
            if panel.get("type") == "row" or not panel.get("targets"):
                continue
            datasource = panel["datasource"]["uid"]
            target = panel["targets"][0].copy()
            if datasource in {"pc-stats", "pc-alerts"}:
                target["datasource"] = panel["datasource"]
                if "url" in target:
                    target["url"] = target["url"].replace("${scope_id:percentencode}", "").replace("${period}", "30d")
                result = grafana.post("/api/ds/query", json={"queries": [target], "from": "now-1h", "to": "now"})
                result.raise_for_status()
                require(not result.json()["results"]["A"].get("error"), panel["title"])
                continue
            expression = target.get("expr", target.get("query", ""))
            expression = re.sub(
                r"\$\{(?:role|instance|operation|family|error_category|provider|capability|execution)(?::[^}]+)?\}",
                ".*",
                expression,
            )
            expression = (
                expression
                .replace("$role", ".*")
                .replace("$instance", ".*")
                .replace("$operation", ".*")
                .replace("$family", ".*")
                .replace("$error_category", ".*")
                .replace("$provider", ".*")
                .replace("$capability", ".*")
                .replace("$execution", ".*")
                .replace("${scope_id:regex}", ".*")
                .replace("${scope_id:percentencode}", "")
                .replace("${period}", "30d")
                .replace("$__range", "1h")
                .replace("$__rate_interval", "5m")
            )
            if datasource == "tempo":
                result = backend.get("/tempo/api/search", params={"q": expression})
            else:
                path = "/prometheus/api/v1/query" if datasource == "prometheus" else "/loki/loki/api/v1/query_range"
                result = backend.get(path, params={"query": expression})
            result.raise_for_status()
            if datasource != "tempo":
                require(result.json()["status"] == "success", panel["title"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", default="http://127.0.0.1:18080")
    parser.add_argument("--gateway", default="http://127.0.0.1:14318")
    parser.add_argument("--grafana", default="http://127.0.0.1:13000")
    parser.add_argument("--background", help="Optional standalone background listener URL")
    parser.add_argument(
        "--exercise-background", action="store_true", help="Explicitly save one Source in the isolated split demo"
    )
    args = parser.parse_args()
    if args.exercise_background and (not args.background or args.server != "http://127.0.0.1:18081"):
        parser.error("--exercise-background requires the isolated container demo and --background")
    local = Path(__file__).resolve().parent / ".local"
    report = []
    with (
        httpx.Client(
            base_url=args.gateway,
            auth=("collector", (local / "gateway-password").read_text().strip()),
            trust_env=False,
            timeout=10,
        ) as backend,
        httpx.Client(
            base_url=args.server,
            headers={"Authorization": "Bearer " + (local / "observe-token").read_text().strip()},
            trust_env=False,
            timeout=10,
        ) as server,
        httpx.Client(
            base_url=args.grafana,
            auth=("admin", (local / "grafana-password").read_text().strip()),
            trust_env=False,
            timeout=10,
        ) as grafana,
    ):
        for client, route in (
            (server, "/health/ready"),
            (grafana, "/api/health"),
            (backend, "/prometheus/-/ready"),
            (backend, "/tempo/ready"),
            (backend, "/loki/ready"),
        ):
            wait_for(lambda client=client, route=route: client.get(route).status_code == 200)
        for route in ("/v1/traces", "/prometheus/api/v1/query", "/tempo/api/search", "/loki/ready"):
            response = httpx.get(args.gateway + route, trust_env=False, timeout=5)
            require(response.status_code == 401, f"Anonymous access accepted: {route}")
        require(httpx.get(args.server + "/metrics", trust_env=False).status_code == 401)
        report.append({"check": "scrape and export authentication", "result": "pass"})
        verify_provisioning(grafana, backend)
        report.append({"check": "provisioned datasources and dashboards", "result": "pass"})
        trace_id = secrets.token_hex(16)
        response = server.get("/v1/capabilities", headers={"traceparent": f"00-{trace_id}-{secrets.token_hex(8)}-01"})
        response.raise_for_status()

        def stored_trace():
            result = backend.get("/tempo/api/traces/" + trace_id, headers={"Accept": "application/json"})
            return result.json() if result.status_code == 200 else None

        trace = wait_for(stored_trace)
        require("get_capabilities" in json.dumps(trace))
        report.append({
            "check": "sampled HTTP request stored and queried in Tempo",
            "result": "pass",
            "trace_id": trace_id,
        })

        def stored_log():
            result = backend.get(
                "/loki/loki/api/v1/query_range",
                params={
                    "query": '{service_name="powercontext-server"} |= "' + trace_id + '"',
                    "limit": 20,
                },
            )
            result.raise_for_status()
            return result.json()["data"]["result"]

        logs = wait_for(stored_log)
        require("get_capabilities" in json.dumps(logs))
        report.append({"check": "same request trace_id queried from Loki", "result": "pass"})

        def ingested_metric():
            result = backend.get(
                "/prometheus/api/v1/query",
                params={
                    "query": 'powercontext_server_transport_requests_total{operation="get_capabilities"}',
                },
            )
            result.raise_for_status()
            return result.json()["data"]["result"]

        require(wait_for(ingested_metric))

        def stored_exemplar():
            result = backend.get(
                "/prometheus/api/v1/query_exemplars",
                params={
                    "query": 'powercontext_server_application_operation_duration_seconds_bucket{operation="get_capabilities"}',
                    "start": time.time() - 300,
                    "end": time.time(),
                },
            )
            result.raise_for_status()
            return trace_id in result.text

        wait_for(stored_exemplar)
        report.append({"check": "real scraped metric and sampled exemplar stored in Prometheus", "result": "pass"})
        require(server.get("/health/ready").status_code == 200)
        if args.background:
            report.extend(verify_background(server, backend, args.background, args.exercise_background))
    path = local / "verification.json"
    serialized = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    path.write_text(serialized)
    mode = (
        "split-work"
        if args.exercise_background
        else "split"
        if args.background
        else "container"
        if args.server.endswith(":18081")
        else "host"
    )
    (local / f"verification-{mode}.json").write_text(serialized)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
