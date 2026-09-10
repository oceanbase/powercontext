"""Run one local service in the foreground with isolated settings and logs."""

import argparse
import os
from pathlib import Path

from prepare import ROOT, STATE


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("service", choices=["migrate", "api", "worker", "agent", "web", "pc", "pc-tools", "pc-agent"])
    parser.add_argument("--dify", type=Path, default=ROOT.parent / "dify")
    parser.add_argument("--api-env", type=Path, default=STATE / "venvs/api")
    parser.add_argument("--agent-env", type=Path, default=STATE / "venvs/agent")
    args = parser.parse_args()
    service = args.service
    name = "api" if service in {"migrate", "worker"} else service
    if service in {"pc-tools", "pc-agent"}:
        name = "plugin"
    environment = os.environ.copy()
    environment.update(
        line.split("=", 1)
        for line in (STATE / f"{name}.env").read_text().splitlines()
        if line and not line.startswith("#")
    )
    environment.update(PYTHONUNBUFFERED="1", UV_CACHE_DIR="/private/tmp/pc-dify-uv")
    if service in {"migrate", "api", "worker"}:
        environment["UV_PROJECT_ENVIRONMENT"] = str(args.api_env)
        command = ["uv", "run", "--no-sync", "--project", str(args.dify / "api")]
        if service == "migrate":
            command += ["flask", "db", "upgrade"]
        elif service == "api":
            command += ["flask", "run", "--host", "127.0.0.1", "--port", "31501", "--no-reload"]
        else:
            command += [
                "celery",
                "-A",
                "app.celery",
                "worker",
                "-P",
                "gevent",
                "-c",
                "2",
                "--loglevel",
                "INFO",
                "-Q",
                "plugin,workflow,workflow_storage,conversation,workflow_based_app_execution",
            ]
        directory = args.dify / "api"
    elif service == "agent":
        command = [
            str(args.agent_env / "bin/uvicorn"),
            "dify_agent.server.app:app",
            "--host",
            "127.0.0.1",
            "--port",
            "31505",
        ]
        directory = args.dify / "dify-agent"
    elif service == "web":
        command = ["pnpm", "exec", "next", "dev", "--hostname", "127.0.0.1", "--port", "31300"]
        directory = args.dify / "web"
    elif service in {"pc-tools", "pc-agent"}:
        package = "powercontext" if service == "pc-tools" else "powercontext_agent"
        command = [str(ROOT / "integrations/dify/.venv/bin/python"), "main.py"]
        directory = ROOT / "integrations/dify" / package
    else:
        command = [str(ROOT / ".venv/bin/powercontext"), "server", "run", "--env-file", str(STATE / "pc.env")]
        directory = ROOT
    os.chdir(directory)
    (STATE / f"{service}.pid").write_text(str(os.getpid()))
    os.execvpe(command[0], command, environment)  # noqa: S606 -- explicit local service commands


if __name__ == "__main__":
    main()
