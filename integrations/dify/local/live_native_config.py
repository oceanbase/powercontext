"""Verify native V2 memory configuration through a running CE Console API."""

import json

from console import Console
from prepare import STATE


def main():
    console = Console()
    path = STATE / "agent-app.json"
    if path.exists():
        agent_id = json.loads(path.read_text())["id"]
    else:
        app = console.post("/agent", json={"name": "PC Memory Acceptance"})
        agent_id = app["id"]
        path.write_text(json.dumps({"id": agent_id}) + "\n")
    credential = json.loads((STATE / "provider.json").read_text())["credentials"][0]["id"]

    def tool(name):
        return {
            "provider_type": "plugin",
            "plugin_id": "oceanbase/powercontext",
            "provider": "powercontext",
            "provider_id": "oceanbase/powercontext/powercontext",
            "tool_name": name,
            "credential_type": "api-key",
            "credential_ref": {"type": "provider", "id": credential},
        }

    current = console.get(f"/agent/{agent_id}/composer")
    soul = current["agent_soul"]
    soul["tools"]["dify_tools"] = [tool(name) for name in ("prepare_context", "capture_event", "search_memory")]
    soul["memory"]["external"] = {
        "prepare": tool("prepare_context"),
        "observe": tool("capture_event"),
        "subject_kind": "user",
        "max_bytes": 8000,
        "capture_max_bytes": 8192,
        "capture": True,
    }
    console.check(
        console.client.put(
            f"/agent/{agent_id}/composer",
            json={
                "variant": "agent_app",
                "agent_soul": soul,
                "save_strategy": "save_to_current_version",
            },
        )
    )
    loaded = console.get(f"/agent/{agent_id}/composer")
    assert loaded["agent_soul"]["memory"]["external"]["prepare"]["credential_ref"]["id"] == credential
    assert console.secrets["PC_TOKEN"] not in json.dumps(loaded)
    (STATE / "native-config.json").write_text(json.dumps(loaded, ensure_ascii=False, indent=2) + "\n")
    print("PASS: Native memory config saved and reloaded; credential reference retained, PC token absent.")


if __name__ == "__main__":
    main()
