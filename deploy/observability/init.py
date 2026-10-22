"""Create private, local-only observability configuration without overwriting secrets."""

from __future__ import annotations

import argparse
import base64
import json
import secrets
import shutil
import subprocess
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target", default="host.docker.internal:18080", help="API/all metrics target as seen by Docker"
    )
    parser.add_argument("--background-target", help="Optional standalone background metrics target; requires OceanBase")
    parser.add_argument("--split", action="store_true", help="Generate isolated OceanBase demo credentials")
    args = parser.parse_args()
    local = Path(__file__).resolve().parent / ".local"
    local.mkdir(mode=0o700, exist_ok=True)
    local.chmod(0o700)
    (local / "logs").mkdir(exist_ok=True)
    for name in ("observe-token", "gateway-password", "grafana-password"):
        path = local / name
        if not path.exists():
            path.write_text(secrets.token_urlsafe(32))
            path.chmod(0o600)
    password = (local / "gateway-password").read_text().strip()
    digest = subprocess.run(  # noqa: S603 - resolved OpenSSL path with fixed arguments.
        [shutil.which("openssl") or "/usr/bin/openssl", "passwd", "-apr1", "-stdin"],
        input=password,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    (local / "htpasswd").write_text(f"collector:{digest}\n")
    # The containers must be able to read these explicit mounts. The parent remains private.
    for name in ("htpasswd", "grafana-password", "observe-token"):
        (local / name).chmod(0o644)
    targets = [{"targets": [args.target], "labels": {"role": "all"}}]
    if args.background_target:
        targets[0]["labels"]["role"] = "api"
        targets.append({"targets": [args.background_target], "labels": {"role": "background"}})
    (local / "targets.json").write_text(json.dumps(targets, ensure_ascii=False, indent=2) + "\n")
    from powercontext.observability.deployment import initialize_statistics

    initialize_statistics(
        local.parent, server_url="http://" + args.target, token=(local / "observe-token").read_text().strip()
    )
    authorization = base64.b64encode(f"collector:{password}".encode()).decode()
    token = (local / "observe-token").read_text().strip()
    environment = local / "server.env"
    if not environment.exists():
        environment.write_text(
            "POWERCONTEXT_SERVER_LOGGING_FORMAT=json\n"
            "POWERCONTEXT_SERVER_TRACING_ENABLED=true\n"
            "POWERCONTEXT_SERVER_ACCESS_MODE=enforced\n"
            f"POWERCONTEXT_SERVER_AUTH_TOKEN={token}\n"
            "OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:14318\n"
            f"OTEL_EXPORTER_OTLP_HEADERS=Authorization=Basic%20{authorization}\n"
            "OTEL_EXPORTER_OTLP_TIMEOUT=2\n"
            "OTEL_BSP_MAX_QUEUE_SIZE=512\n"
            "OTEL_BSP_MAX_EXPORT_BATCH_SIZE=128\n"
            "OTEL_BSP_SCHEDULE_DELAY=1000\n"
            "OTEL_TRACES_SAMPLER=parentbased_traceidratio\n"
            "OTEL_TRACES_SAMPLER_ARG=0.1\n"
        )
        environment.chmod(0o600)
    if args.split:
        split = local / "split.env"
        if not split.exists():
            password = secrets.token_urlsafe(32)
            split.write_text(
                f"POWERCONTEXT_REFERENCE_DATABASE_PASSWORD={password}\n"
                "POWERCONTEXT_REFERENCE_DATABASE_URL=mysql+aoceanbase://root%40test:"
                f"{password}@reference-oceanbase:2881/powercontext?charset=utf8mb4\n"
            )
            split.chmod(0o600)
    print(f"Configuration ready: {local}. No existing secrets or server.env were replaced.")


if __name__ == "__main__":
    main()
