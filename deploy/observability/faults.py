"""Explicitly interrupt only the reference telemetry containers and verify business isolation."""

from __future__ import annotations

import argparse
import json
import secrets
import shutil
import subprocess
import time
from pathlib import Path

import httpx
from verify import require, wait_for


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container-startup", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    local = root / ".local"
    docker = shutil.which("docker") or "/Applications/Docker.app/Contents/Resources/bin/docker"
    compose = [docker, "compose", "-f", str(root / "compose.yaml")]
    report = []
    with (
        httpx.Client(
            base_url="http://127.0.0.1:18080",
            trust_env=False,
            timeout=5,
            headers={"Authorization": "Bearer " + (local / "observe-token").read_text().strip()},
        ) as server,
        httpx.Client(
            base_url="http://127.0.0.1:14318",
            trust_env=False,
            timeout=5,
            auth=("collector", (local / "gateway-password").read_text().strip()),
        ) as backend,
    ):
        retained_trace_id = secrets.token_hex(16)
        server.get(
            "/v1/capabilities",
            headers={
                "traceparent": f"00-{retained_trace_id}-{secrets.token_hex(8)}-01",
            },
        ).raise_for_status()
        wait_for(lambda: backend.get("/tempo/api/traces/" + retained_trace_id).status_code == 200)
        for component in ("collector", "tempo", "loki"):
            print(f"Interrupting reference {component}; checking business requests and pipeline alert.", flush=True)
            subprocess.run([*compose, "stop", "-t", "3", component], check=True, capture_output=True)  # noqa: S603 - fixed reference project and component allowlist.
            try:
                if args.container_startup and component == "collector":
                    subprocess.run(  # noqa: S603 - fixed isolated reference service.
                        [*compose, "-f", str(root / "compose.container.yaml"), "restart", "reference-server"],
                        check=True,
                        capture_output=True,
                    )
                    wait_for(
                        lambda: httpx.get("http://127.0.0.1:18081/health/ready", trust_env=False).status_code == 200
                    )
                for _ in range(10):
                    trace_id = secrets.token_hex(16)
                    started = time.monotonic()
                    response = server.get(
                        "/v1/capabilities",
                        headers={
                            "traceparent": f"00-{trace_id}-{secrets.token_hex(8)}-01",
                        },
                    )
                    response.raise_for_status()
                    require(time.monotonic() - started < 2, "Telemetry outage blocked a business request")
                require(server.get("/health/ready").status_code == 200)

                def component_down(component=component):
                    result = backend.get("/prometheus/api/v1/query", params={"query": f'up{{job="{component}"}}'})
                    return any(item["value"][1] == "0" for item in result.json()["data"]["result"])

                wait_for(component_down, timeout=30)

                def alert_firing(component=component):
                    result = backend.get(
                        "/prometheus/api/v1/query",
                        params={
                            "query": f'ALERTS{{alertname="TelemetryTargetDown",alertstate="firing",job="{component}"}}',
                        },
                    )
                    return result.json()["data"]["result"]

                wait_for(alert_firing, timeout=90)
            finally:
                subprocess.run([*compose, "start", component], check=True, capture_output=True)  # noqa: S603 - fixed reference project and component allowlist.

            def resolved(component=component):
                result = backend.get(
                    "/prometheus/api/v1/query",
                    params={
                        "query": f'up{{job="{component}"}}',
                    },
                )
                up = any(item["value"][1] == "1" for item in result.json()["data"]["result"])
                alerts = backend.get(
                    "/prometheus/api/v1/query",
                    params={
                        "query": f'ALERTS{{alertname="TelemetryTargetDown",alertstate="firing",job="{component}"}}',
                    },
                ).json()["data"]["result"]
                return up and not alerts

            wait_for(resolved, timeout=60)
            # Recovery must produce newly queryable data; no promise to recover expired batches.
            trace_id = secrets.token_hex(16)
            server.get(
                "/v1/capabilities",
                headers={
                    "traceparent": f"00-{trace_id}-{secrets.token_hex(8)}-01",
                },
            ).raise_for_status()
            wait_for(lambda trace_id=trace_id: backend.get("/tempo/api/traces/" + trace_id).status_code == 200)
            wait_for(
                lambda trace_id=trace_id: backend.get(
                    "/loki/loki/api/v1/query_range",
                    params={
                        "query": '{service_name="powercontext-server"} |= "' + trace_id + '"',
                    },
                ).json()["data"]["result"]
            )
            report.append({
                "component": component,
                "business_requests": 10,
                "request_budget_seconds": 2,
                "alert_fired_and_resolved": True,
                "new_trace_and_log_query_after_recovery": True,
            })
            (local / "fault-verification.json").write_text(json.dumps(report, indent=2) + "\n")
        require(
            backend.get("/tempo/api/traces/" + retained_trace_id).status_code == 200,
            "A retained trace did not survive backend stop/start",
        )
        report.append({
            "retained_trace_survived_backend_restarts": True,
            "retained_trace_id": retained_trace_id,
            "container_started_without_collector": args.container_startup,
        })
        (local / "fault-verification.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
