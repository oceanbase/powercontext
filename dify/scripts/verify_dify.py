#!/usr/bin/env python3
"""Run the same Dify workflow twice and print evidence without printing credentials."""

from __future__ import annotations

import json
import os
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


BASE_URL = os.getenv("DIFY_BASE_URL", "http://localhost").rstrip("/")
API_KEY = os.getenv("DIFY_API_KEY", "")
PLAYER_ID = os.getenv("TIME_ECHO_PLAYER_ID", "echo-review-001")


def run_workflow(choice: str) -> dict[str, Any]:
    request = Request(
        f"{BASE_URL}/v1/workflows/run",
        data=json.dumps(
            {
                "inputs": {
                    "player_id": PLAYER_ID,
                    "player_name": "评审员",
                    "choice": choice,
                },
                "response_mode": "blocking",
                "user": "time-echo-verifier",
            },
            ensure_ascii=False,
        ).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=180) as response:
            payload = json.load(response)
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Dify returned HTTP {error.code}: {detail}") from error
    except URLError as error:
        raise RuntimeError(f"Cannot reach Dify at {BASE_URL}: {error.reason}") from error
    return payload.get("data", {}).get("outputs", {})


def summarize(round_number: int, outputs: dict[str, Any]) -> dict[str, Any]:
    return {
        "round": round_number,
        "player_id": outputs.get("player_id"),
        "recall_status": outputs.get("recall_status"),
        "recall_http_status": outputs.get("recall_http_status"),
        "memory_write_http_status": outputs.get("memory_write_http_status"),
        "story": outputs.get("story"),
        "memory_write_receipt_present": bool(outputs.get("memory_write_receipt")),
    }


def main() -> int:
    if not API_KEY or API_KEY.startswith("app-replace"):
        raise RuntimeError("Set DIFY_API_KEY to the imported workflow application's API key")
    first = run_workflow("我把会说话的怀表交给了守钟人，但偷偷记下了表盘上的坐标。")
    second = run_workflow("我沿着怀表坐标潜入午夜档案室，要求守钟人兑现承诺。")
    print(json.dumps([summarize(1, first), summarize(2, second)], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1) from error

