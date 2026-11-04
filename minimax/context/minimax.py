"""MiniMax boundary.

MiniMax can participate in three ways; the system supports all of them and records
which one produced each artifact:

``api``    -- MiniMax ChatCompletion over HTTPS.  The key is read from
              ``MINIMAX_API_KEY`` or, when that is empty, from the file named by
              ``MINIMAX_API_KEY_FILE`` (default ``~/.minimax_key``) so it never has to
              sit in shell history.  It never leaves this process and never enters the
              database.
``cli``    -- the MiniMax agent CLI, driven as ``MINIMAX_AGENT_CMD`` from a prepared
              prompt file.
``manual`` -- the developer pastes the prepared prompt into MiniMax and drops the
              answer back; the response text is read from ``MINIMAX_AGENT_OUTPUT``.

Every path produces the same thing: a *patch proposal* (one or more unified diffs)
that this process validates before it is applied.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

DEFAULT_BASE_URL = "https://api.minimaxi.com/v1"
DEFAULT_MODEL = "MiniMax-Text-01"
CHAT_PATH = "/chat/completions"


@dataclass
class AgentRun:
    backend: str
    model: str
    prompt_chars: int
    output: str
    ok: bool
    error: str = ""

    def to_record(self) -> dict:
        return {
            "backend": self.backend,
            "model": self.model,
            "prompt_chars": self.prompt_chars,
            "ok": self.ok,
            "error": self.error,
            "output_chars": len(self.output),
            "output_sha": _digest(self.output),
        }


def _digest(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def prepare_prompt(context_text: str, instruction: str) -> str:
    return (
        "You are changing the seekdb C++ server (OceanBase SQL engine).\n"
        "Below is the developer context assembled for this task: the specs that apply,\n"
        "the exact code locations, the existing tests that must keep passing, and what the\n"
        "running server does today.\n\n"
        "Rules:\n"
        "1. Only satisfy the 'must-change' specs. Never change behaviour covered by a\n"
        "   'must-keep' spec.\n"
        "2. Output unified diffs only, one per file, each starting with 'diff --git'.\n"
        "3. Keep the change small and explain in one sentence per file why it is needed.\n\n"
        f"Task instruction: {instruction}\n\n"
        f"{context_text}\n"
    )


def api_key() -> str:
    """Read the key from the environment, or from a file when the env var is empty.

    ``MINIMAX_API_KEY_FILE`` (default ``~/.minimax_key``) keeps the secret out of shell
    history and out of any transcript: write the key to that file once and leave the
    environment variable unset.
    """
    value = os.environ.get("MINIMAX_API_KEY", "").strip()
    if value:
        return value
    path = Path(os.environ.get("MINIMAX_API_KEY_FILE", "~/.minimax_key")).expanduser()
    if path.is_file():
        return path.read_text(encoding="utf-8").strip()
    return ""


def run_api(prompt: str) -> AgentRun:
    key = api_key()
    model = os.environ.get("MINIMAX_MODEL", DEFAULT_MODEL)
    base_url = os.environ.get("MINIMAX_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    if not key:
        return AgentRun(
            "api", model, len(prompt), "", False,
            "no API key: set MINIMAX_API_KEY or write it to ~/.minimax_key "
            "(or set MINIMAX_API_KEY_FILE)",
        )

    body = json.dumps(
        {
            "model": model,
            "messages": [
                {"role": "system", "content": "You are a precise C++ engineer. Reply with diffs only."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.1,
            "max_tokens": int(os.environ.get("MINIMAX_MAX_TOKENS", "8192")),
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}{CHAT_PATH}",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=int(os.environ.get("MINIMAX_TIMEOUT", "180"))) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        return AgentRun("api", model, len(prompt), "", False, f"HTTP {exc.code}: {detail}")
    except Exception as exc:  # noqa: BLE001 - transport errors are recorded, not raised
        return AgentRun("api", model, len(prompt), "", False, f"{type(exc).__name__}: {exc}")

    choices = payload.get("choices") or []
    if not choices:
        return AgentRun("api", model, len(prompt), "", False, f"unexpected response: {str(payload)[:300]}")
    output = choices[0].get("message", {}).get("content", "")
    return AgentRun("api", model, len(prompt), output, bool(output.strip()))


def run_cli(prompt: str, workdir: Path | None = None) -> AgentRun:
    command = os.environ.get("MINIMAX_AGENT_CMD", "")
    if not command:
        return AgentRun("cli", "", len(prompt), "", False, "MINIMAX_AGENT_CMD is not set")
    prompt_file = (workdir or Path.cwd()) / "minimax-prompt.md"
    prompt_file.write_text(prompt, encoding="utf-8")
    proc = subprocess.run(
        f'{command} "{prompt_file}"',
        shell=True,
        text=True,
        capture_output=True,
        cwd=str(workdir) if workdir else None,
    )
    output = proc.stdout or ""
    ok = proc.returncode == 0 and bool(output.strip())
    return AgentRun("cli", command, len(prompt), output, ok,
                    "" if ok else f"exit {proc.returncode}: {(proc.stderr or '')[:300]}")


def run_manual(prompt: str) -> AgentRun:
    path = os.environ.get("MINIMAX_AGENT_OUTPUT", "")
    if not path:
        return AgentRun("manual", "", len(prompt), "", False,
                        "MINIMAX_AGENT_OUTPUT is not set (paste MiniMax's answer into a file)")
    output = Path(path).read_text(encoding="utf-8")
    return AgentRun("manual", "MiniMax Code (desktop)", len(prompt), output, bool(output.strip()))


def dispatch(prompt: str, workdir: Path | None = None) -> AgentRun:
    """Prefer a configured API key, then the configured CLI, then a pasted answer."""
    if api_key():
        run = run_api(prompt)
        if run.ok:
            return run
        fallback = run_cli(prompt, workdir) if os.environ.get("MINIMAX_AGENT_CMD") else run_manual(prompt)
        if fallback.ok:
            fallback.error = f"api failed ({run.error}); used {fallback.backend}"
            return fallback
        return run
    if os.environ.get("MINIMAX_AGENT_CMD"):
        run = run_cli(prompt, workdir)
        if run.ok:
            return run
        fallback = run_manual(prompt)
        if fallback.ok:
            fallback.error = f"cli failed ({run.error}); used manual"
            return fallback
        return run
    return run_manual(prompt)


# Patch extraction lives in ``patchutil`` because the agent driver and the operations
# layer must agree on exactly what a proposal is.
from context.patchutil import extract_patch, files_in, measure  # noqa: E402  (re-export)


def digest(text: str) -> str:
    """Short content hash used in the usage log (never the content itself)."""
    return _digest(text)
