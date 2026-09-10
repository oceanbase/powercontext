"""Authenticated HTTP access to the isolated acceptance workspace."""

import base64
import json

import httpx
from prepare import STATE


class Console:
    def __init__(self):
        self.secrets = json.loads((STATE / "credentials.json").read_text())
        self.client = httpx.Client(base_url="http://127.0.0.1:31501/console/api", timeout=120)
        status = self.client.get("/setup")
        status.raise_for_status()
        if status.json()["step"] == "not_started":
            self.check(
                self.client.post(
                    "/setup",
                    json={
                        "email": "pc-dify@example.com",
                        "name": "PC Local Acceptance",
                        "password": self.secrets["ADMIN_PASSWORD"],
                        "language": "zh-Hans",
                    },
                )
            )
        self.check(
            self.client.post(
                "/login",
                json={
                    "email": "pc-dify@example.com",
                    "password": base64.b64encode(self.secrets["ADMIN_PASSWORD"].encode()).decode(),
                },
            )
        )
        for key, value in self.client.cookies.items():
            if "csrf" in key:
                self.client.headers["X-CSRF-Token"] = value

    def check(self, response):
        if not response.is_success:
            message = response.text
            for value in self.secrets.values():
                message = message.replace(value, "[REDACTED]")
            raise RuntimeError(f"HTTP {response.status_code}: {message[:1000]}")  # noqa: TRY003
        return response.json()

    def get(self, path, **kwargs):
        return self.check(self.client.get(path, **kwargs))

    def post(self, path, **kwargs):
        return self.check(self.client.post(path, **kwargs))


if __name__ == "__main__":
    console = Console()
    debug = console.get("/workspaces/current/plugin/debugging-key")
    target = STATE / "plugin.env"
    target.write_text(
        "INSTALL_METHOD=remote\nREMOTE_INSTALL_HOST=127.0.0.1\nREMOTE_INSTALL_PORT=31503\n"
        f"REMOTE_INSTALL_KEY={debug['key']}\n"
    )
    target.chmod(0o600)
    print("Local workspace created; console login and plugin debugging-key request succeeded.")
