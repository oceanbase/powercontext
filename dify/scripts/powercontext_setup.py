#!/usr/bin/env python3
"""Create a trusted PowerContext scope and optionally verify remember/prepare."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def post(base_url: str, token: str | None, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(
        f"{base_url.rstrip('/')}{path}",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urlopen(request, timeout=20) as response:
            return json.load(response)
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"PowerContext {path} returned HTTP {error.code}: {detail}") from error
    except URLError as error:
        raise RuntimeError(f"Cannot reach PowerContext at {base_url}: {error.reason}") from error


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=os.getenv("POWERCONTEXT_BASE_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--token", default=os.getenv("POWERCONTEXT_TOKEN", ""))
    parser.add_argument("--idempotency-key", default="time-echo-archive-v1")
    parser.add_argument("--seed-player", default="", help="Also write and recall one harmless test memory.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    scope = post(
        args.base_url,
        args.token or None,
        "/v1/scopes",
        {
            "title": "Time Echo Archive",
            "summary": "Dify interactive-story decisions and their generated consequences.",
            "idempotency_key": args.idempotency_key,
        },
    )
    scope_id = scope["scope_id"]
    output: dict[str, Any] = {
        "scope_id": scope_id,
        "dify_environment_variable": "POWERCONTEXT_SCOPE_ID",
    }

    if args.seed_player:
        player_id = args.seed_player.strip()[:48]
        saved = post(
            args.base_url,
            args.token or None,
            "/v1/memory/remember",
            {
                "scope_id": scope_id,
                "kind": "decision",
                "text": f"player_id {player_id}\nsetup_check The archive clock was wound once.",
                "reason": "Reproducible setup smoke test requested by the operator.",
            },
        )
        prepared = post(
            args.base_url,
            args.token or None,
            "/v1/context/prepare",
            {"scope_id": scope_id, "query": f"player_id {player_id}", "max_bytes": 2000},
        )
        output["smoke_test"] = {
            "saved_record_count": len(saved.get("records") or []),
            "prepare_status": prepared.get("status"),
            "content_contains_player_id": player_id in (prepared.get("content") or ""),
        }

    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (KeyError, RuntimeError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1) from error

