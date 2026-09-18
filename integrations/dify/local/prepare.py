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

"""Prepare isolated local CE settings without copying existing user credentials."""

import base64
import json
import secrets
from pathlib import Path

LOCAL = Path(__file__).resolve().parent
ROOT = LOCAL.parents[2]
STATE = ROOT / ".powercontext-e2e" / "dify"


def write_env(path: Path, values: dict[str, str]) -> None:
    path.write_text("".join(f"{key}={value}\n" for key, value in values.items()))
    path.chmod(0o600)


def prepare() -> None:
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    secret_file = STATE / "credentials.json"
    if secret_file.exists():
        config = json.loads(secret_file.read_text())
    else:
        config = {
            key: secrets.token_urlsafe(32)
            for key in ("DATABASE_PASSWORD", "DAEMON_KEY", "INNER_KEY", "AGENT_TOKEN", "SANDBOX_TOKEN", "PC_TOKEN")
        }
        config["SECRET_KEY"] = base64.b64encode(secrets.token_bytes(32)).decode()
        config["ADMIN_PASSWORD"] = "Pc9!" + secrets.token_urlsafe(20)
        secret_file.write_text(json.dumps(config, indent=2) + "\n")
        secret_file.chmod(0o600)
    write_env(LOCAL / ".env", config)
    write_env(
        STATE / "api.env",
        {
            "DEPLOYMENT_EDITION": "COMMUNITY",
            "LOG_FORMAT": "%(asctime)s %(levelname)s %(name)s %(message)s",
            "LOG_OUTPUT_FORMAT": "text",
            "SECRET_KEY": config["SECRET_KEY"],
            "CONSOLE_API_URL": "http://127.0.0.1:31501",
            "CONSOLE_WEB_URL": "http://127.0.0.1:31300",
            "SERVICE_API_URL": "http://127.0.0.1:31501",
            "APP_WEB_URL": "http://127.0.0.1:31300",
            "FILES_URL": "http://127.0.0.1:31501",
            "INTERNAL_FILES_URL": "http://host.docker.internal:31501",
            "CONSOLE_CORS_ALLOW_ORIGINS": "http://127.0.0.1:31300",
            "WEB_API_CORS_ALLOW_ORIGINS": "http://127.0.0.1:31300",
            "DB_TYPE": "postgresql",
            "DB_HOST": "127.0.0.1",
            "DB_PORT": "31432",
            "DB_USERNAME": "postgres",
            "DB_PASSWORD": config["DATABASE_PASSWORD"],
            "DB_DATABASE": "dify",
            "REDIS_HOST": "127.0.0.1",
            "REDIS_PORT": "31379",
            "REDIS_PASSWORD": "",
            "CELERY_BROKER_URL": "redis://127.0.0.1:31379/1",
            "STORAGE_TYPE": "opendal",
            "OPENDAL_SCHEME": "fs",
            "OPENDAL_FS_ROOT": str(STATE / "storage"),
            "VECTOR_STORE": "pgvector",
            "PGVECTOR_HOST": "127.0.0.1",
            "PGVECTOR_PORT": "31432",
            "PGVECTOR_USER": "postgres",
            "PGVECTOR_PASSWORD": config["DATABASE_PASSWORD"],
            "PGVECTOR_DATABASE": "dify",
            "PLUGIN_DAEMON_URL": "http://127.0.0.1:31502",
            "PLUGIN_DAEMON_KEY": config["DAEMON_KEY"],
            "PLUGIN_REMOTE_INSTALL_HOST": "127.0.0.1",
            "PLUGIN_REMOTE_INSTALL_PORT": "31503",
            "INNER_API_KEY_FOR_PLUGIN": config["INNER_KEY"],
            "AGENT_BACKEND_BASE_URL": "http://127.0.0.1:31505",
            "AGENT_BACKEND_API_TOKEN": config["AGENT_TOKEN"],
            "NEW_USER_DEFAULT_PLUGIN_IDS": "",
            "ENABLE_OTEL": "false",
            "SSRF_PROXY_HTTP_URL": "",
            "SSRF_PROXY_HTTPS_URL": "",
            "INIT_PASSWORD": "",
            "SENTRY_DSN": "",
        },
    )
    write_env(
        STATE / "agent.env",
        {
            "DIFY_AGENT_REDIS_URL": "redis://127.0.0.1:31379/2",
            "DIFY_AGENT_PLUGIN_DAEMON_URL": "http://127.0.0.1:31502",
            "DIFY_AGENT_PLUGIN_DAEMON_API_KEY": config["DAEMON_KEY"],
            "DIFY_AGENT_INNER_API_URL": "http://127.0.0.1:31501",
            "DIFY_AGENT_INNER_API_KEY": config["INNER_KEY"],
            "DIFY_AGENT_API_TOKEN": config["AGENT_TOKEN"],
            "DIFY_AGENT_SERVER_SECRET_KEY": config["SECRET_KEY"].rstrip("=").replace("+", "-").replace("/", "_"),
            "DIFY_AGENT_LOCAL_SANDBOX_ENDPOINT": "http://127.0.0.1:31504",
            "DIFY_AGENT_LOCAL_SANDBOX_AUTH_TOKEN": config["SANDBOX_TOKEN"],
            "DIFY_AGENT_STUB_API_BASE_URL": "http://host.docker.internal:31505/agent-stub",
            "DIFY_AGENT_SANDBOX_FILES_BASE_URL": "http://host.docker.internal:31501",
        },
    )
    write_env(
        STATE / "web.env",
        {
            "NEXT_PUBLIC_DEPLOY_ENV": "DEVELOPMENT",
            "NEXT_TELEMETRY_DISABLED": "1",
            "CONSOLE_API_URL": "http://127.0.0.1:31501",
            "NEXT_PUBLIC_API_PREFIX": "http://127.0.0.1:31501/console/api",
            "NEXT_PUBLIC_PUBLIC_API_PREFIX": "http://127.0.0.1:31501/api",
            "NEXT_PUBLIC_SOCKET_URL": "ws://127.0.0.1:31501",
        },
    )
    pc_env = STATE / "pc.env"
    if not pc_env.exists():
        write_env(
            pc_env,
            {
                "POWERCONTEXT_HOME": str(STATE / "pc-data"),
                "POWERCONTEXT_SERVER_HTTP_HOST": "127.0.0.1",
                "POWERCONTEXT_SERVER_HTTP_PORT": "31800",
                "POWERCONTEXT_SERVER_ACCESS_MODE": "enforced",
                "POWERCONTEXT_SERVER_AUTH_TOKEN": config["PC_TOKEN"],
                "POWERCONTEXT_SERVER_ACCESS_DEPLOYMENT_ID": "dify-local-acceptance",
                "POWERCONTEXT_SERVER_ACCESS_BACKGROUND_PRINCIPAL_ID": "service:dify-local-processing",
            },
        )
    print(f"Local settings prepared in {STATE}; credentials were not printed.")


if __name__ == "__main__":
    prepare()
