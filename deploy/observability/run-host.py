"""Run an isolated host demo with bounded, collector-readable logs."""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import signal
import subprocess
import sys
from contextlib import suppress
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container", action="store_true")
    parser.add_argument("--log-name", choices=("container", "background"), default="container")
    args = parser.parse_args()
    local = Path("/data") if args.container else Path(__file__).resolve().parent / ".local"
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("POWERCONTEXT_", "OTEL_", "OPENAI_", "ANTHROPIC_"))
    }
    if args.container:
        environment = dict(os.environ)
    for line in [] if args.container else (local / "server.env").read_text().splitlines():
        if line and not line.startswith("#"):
            key, value = line.split("=", 1)
            environment[key] = value
    if not args.container:
        environment.update({
            "POWERCONTEXT_SERVER_DATABASE_KIND": "sqlite",
            "POWERCONTEXT_SERVER_DATABASE_URL": f"sqlite+aiosqlite:///{local / 'host.db'}",
            "POWERCONTEXT_SERVER_HTTP_HOST": "0.0.0.0",  # noqa: S104 - authenticated Docker-to-host demo.
            "POWERCONTEXT_SERVER_HTTP_PORT": "18080",
        })
    handler = logging.handlers.RotatingFileHandler(
        Path(f"/logs/{args.log_name}.jsonl") if args.container else local / "logs" / "host.jsonl",
        maxBytes=10 * 1024 * 1024,
        backupCount=3,
    )
    process = subprocess.Popen(  # noqa: S603 - fixed Server entrypoint, no shell.
        [str(Path(sys.executable).parent / "powercontext"), "server", "run", "--no-env-file"],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    def stop(_number: int, _frame: object) -> None:
        process.terminate()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    if process.stdout is None:
        return 1
    try:
        for line in process.stdout:
            # Collector drops non-JSON startup/library output and exports only allowlisted JSON fields.
            with suppress(Exception):
                handler.emit(logging.LogRecord("server", logging.INFO, "", 0, line.rstrip(), (), None))
        return process.wait()
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        handler.close()


if __name__ == "__main__":
    raise SystemExit(main())
