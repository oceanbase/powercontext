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
    key_source: str = ""

    def to_record(self) -> dict:
        return {
            "backend": self.backend,
            "model": self.model,
            "prompt_chars": self.prompt_chars,
            "ok": self.ok,
            "error": self.error,
            "output_chars": len(self.output),
            "output_sha": _digest(self.output),
            "key_source": self.key_source,
        }


def _digest(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


SYSTEM_PROMPT = (
    "You are an offline C++ patch generator for the OceanBase seekdb SQL engine.\n"
    "You have NO tools, NO shell, NO filesystem and NO repository access in this call.\n"
    "Never try to explore, list files, run commands, or emit tool calls, XML tags such as\n"
    "<input>, or commentary about what you would do. Everything you need is inside the\n"
    "user message: the specs, the file paths with their current text, and the tests.\n"
    "Answer with unified diff blocks only, one per file, using the exact paths shown."
)

OUTPUT_CONTRACT = (
    "OUTPUT CONTRACT (mandatory)\n"
    "- Reply with one or more unified diffs, each starting with 'diff --git a/<path> b/<path>'.\n"
    "- Use only the file paths listed in the context; the 'source:' lines carry current code.\n"
    "- Include correct '@@ -l,s +l,s @@' hunk headers; context lines start with one space.\n"
    "- You may add ONE sentence per file after its diff; no other prose.\n"
    "- Do not output shell commands, XML/HTML tags, or questions. If a file must be created,\n"
    "  say so in that one sentence.\n"
)


def prepare_prompt(context_text: str, instruction: str) -> str:
    return (
        f"{OUTPUT_CONTRACT}\n"
        "Task instruction:\n"
        f"{instruction}\n\n"
        "Rules of the change:\n"
        "1. Only satisfy the 'must-change' specs. Never change behaviour covered by a\n"
        "   'must-keep' spec.\n"
        "2. Keep the change small; do not restate the task.\n\n"
        "Developer context assembled for this task (specs, code locations with current\n"
        "source text, existing tests, and what the running server does today):\n\n"
        f"{context_text}\n\n"
        f"{OUTPUT_CONTRACT}\n"
        "Now output the diffs only.\n"
    )


def api_key_with_source() -> tuple[str, str]:
    """Return ``(key, where it came from)`` without ever logging the key itself.

    Order: ``MINIMAX_API_KEY`` -> ``MINIMAX_API_KEY_FILE`` / ``~/.minimax_key`` ->
    the MiniMax CLI's own store (``~/.mmx/config.json``).  The CLI store is the last
    resort because a Token Plan is usually already activated there, and reusing it
    avoids pasting a key that belongs to a different, unbilled account.
    """
    value = os.environ.get("MINIMAX_API_KEY", "").strip()
    if value:
        return value, "env:MINIMAX_API_KEY"
    path = Path(os.environ.get("MINIMAX_API_KEY_FILE", "~/.minimax_key")).expanduser()
    if path.is_file():
        candidate = path.read_text(encoding="utf-8").strip()
        if candidate:
            return candidate, f"file:{path}"
    mmx = Path("~/.mmx/config.json").expanduser()
    if mmx.is_file():
        try:
            stored = json.loads(mmx.read_text(encoding="utf-8")).get("api_key", "")
        except (OSError, ValueError):
            stored = ""
        if stored:
            return stored.strip(), f"mmx:{mmx}"
    return "", ""


def api_key() -> str:
    """Read the API key from the first configured source."""
    return api_key_with_source()[0]


def run_api(prompt: str) -> AgentRun:
    key, key_source = api_key_with_source()
    model = os.environ.get("MINIMAX_MODEL", DEFAULT_MODEL)
    base_url = os.environ.get("MINIMAX_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    if not key:
        return AgentRun(
            "api", model, len(prompt), "", False,
            "no API key: set MINIMAX_API_KEY, write ~/.minimax_key, "
            "or run 'mmx auth login --api-key ...'",
        )

    body = json.dumps(
        {
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "temperature": float(os.environ.get("MINIMAX_TEMPERATURE", "0.1")),
            # Reasoning tokens are charged against max_tokens. A long context plus the
            # default 8192 can leave an empty message with finish_reason=length, so the
            # budget is explicit and recorded in the run.
            "max_tokens": int(os.environ.get("MINIMAX_MAX_TOKENS", "32768")),
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
        return AgentRun("api", model, len(prompt), "", False, f"HTTP {exc.code}: {detail}", key_source)
    except Exception as exc:  # noqa: BLE001 - transport errors are recorded, not raised
        return AgentRun("api", model, len(prompt), "", False, f"{type(exc).__name__}: {exc}", key_source)

    choices = payload.get("choices") or []
    if not choices:
        return AgentRun("api", model, len(prompt), "", False,
                        f"unexpected response: {str(payload)[:300]}", key_source)
    choice = choices[0]
    output = (choice.get("message") or {}).get("content") or ""
    finish = choice.get("finish_reason", "")
    usage = payload.get("usage") or {}
    reasoning = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
    if not output.strip():
        detail = f"the model returned an empty message (finish_reason={finish}"
        if reasoning is not None:
            detail += f", reasoning_tokens={reasoning}"
        detail += ")"
        if finish == "length":
            detail += "; raise MINIMAX_MAX_TOKENS or shorten the context"
        return AgentRun("api", model, len(prompt), "", False, detail, key_source)
    return AgentRun("api", model, len(prompt), output, True, key_source=key_source)


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
