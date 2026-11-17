#!/usr/bin/env python3
"""Static checks for the competition DSL and accidental credential commits."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
DSL = ROOT / "time-echo-archive.yml"
TEMPLATE_REFERENCE = re.compile(r"\{\{#([^.}]+)\.([^#}]+)#\}\}")
SECRET_PATTERNS = {
    "GitHub token": re.compile(r"\b(?:ghp_|github_pat_)[A-Za-z0-9_]{16,}"),
    "OpenAI-style key": re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def validate_graph(document: dict[str, Any]) -> tuple[int, int]:
    graph = document["workflow"]["graph"]
    nodes = graph["nodes"]
    edges = graph["edges"]
    node_map = {str(node["id"]): node for node in nodes}
    require(len(node_map) == len(nodes), "Node IDs must be unique")

    for edge in edges:
        source = str(edge["source"])
        target = str(edge["target"])
        require(source in node_map, f"Edge source does not exist: {source}")
        require(target in node_map, f"Edge target does not exist: {target}")
        require(edge["data"]["sourceType"] == node_map[source]["data"]["type"], f"Wrong sourceType: {edge['id']}")
        require(edge["data"]["targetType"] == node_map[target]["data"]["type"], f"Wrong targetType: {edge['id']}")

    inbound = {node_id: 0 for node_id in node_map}
    outbound = {node_id: 0 for node_id in node_map}
    for edge in edges:
        inbound[str(edge["target"])] += 1
        outbound[str(edge["source"])] += 1
    for node_id, node in node_map.items():
        node_type = node["data"]["type"]
        require(node_type == "start" or inbound[node_id] > 0, f"Disconnected input: {node_id}")
        require(node_type == "end" or outbound[node_id] > 0, f"Disconnected output: {node_id}")

    serialized = DSL.read_text(encoding="utf-8")
    environment_names = {item["name"] for item in document["workflow"]["environment_variables"]}
    for owner, variable in TEMPLATE_REFERENCE.findall(serialized):
        if owner == "env":
            require(variable in environment_names, f"Unknown environment variable: {variable}")
        elif owner not in {"sys", "conversation"}:
            require(owner in node_map, f"Template references unknown node: {owner}.{variable}")

    for node in nodes:
        if node["data"]["type"] == "code":
            compile(node["data"]["code"], f"dsl-node-{node['id']}", "exec")
    return len(nodes), len(edges)


def validate_secrets() -> int:
    checked = 0
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {".yml", ".yaml", ".md", ".py", ".example"}:
            continue
        text = path.read_text(encoding="utf-8")
        for name, pattern in SECRET_PATTERNS.items():
            require(not pattern.search(text), f"Possible {name} in {path.relative_to(ROOT)}")
        checked += 1
    return checked


def main() -> None:
    document = yaml.safe_load(DSL.read_text(encoding="utf-8"))
    require(document["kind"] == "app", "DSL kind must be app")
    require(document["version"] == "0.3.0", "DSL version must be 0.3.0")
    require(document["app"]["mode"] == "workflow", "This submission expects workflow mode")
    nodes, edges = validate_graph(document)
    files = validate_secrets()
    print(f"OK: DSL 0.3.0, {nodes} nodes, {edges} edges, {files} files scanned")


if __name__ == "__main__":
    main()

