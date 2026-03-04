"""
Utilities for reading and updating .env files.

Goals:
- Preserve existing comments and unrelated keys
- Update only selected keys (first occurrence)
- Append missing keys under a managed section
- Create a timestamped backup before writing
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple


_ENV_ASSIGN_RE = re.compile(r"^\s*(?:export\s+)?(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<value>.*)\s*$")
_ENV_ASSIGN_CAPTURE_RE = re.compile(
    r"^(?P<leading>\s*)(?P<export>export\s+)?(?P<key>[A-Za-z_][A-Za-z0-9_]*)"
    r"(?P<sep>\s*=\s*)(?P<rest>.*?)(?P<eol>\r?\n)?$"
)

def parse_env_lines(lines: Iterable[str]) -> Dict[str, str]:
    """
    Parse KEY=VALUE assignments from .env lines.
    Comments and blank lines are ignored.
    If a key appears multiple times, the first occurrence wins (matching update logic).
    """
    values: Dict[str, str] = {}
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _ENV_ASSIGN_RE.match(raw)
        if not m:
            continue
        key = m.group("key")
        if key in values:
            continue
        value = m.group("value").strip()
        values[key] = _strip_optional_quotes(value)
    return values


def _strip_optional_quotes(value: str) -> str:
    if len(value) >= 2 and ((value[0] == value[-1] == '"') or (value[0] == value[-1] == "'")):
        return value[1:-1]
    return value


def format_env_value(value: str) -> str:
    """
    Format a value for .env output.
    Use double quotes when needed and escape special characters.
    Empty string is written as nothing (KEY=) to avoid OCEANBASE_PASSWORD="" style.
    """
    if value is None:
        return ""
    if value == "":
        return ""
    needs_quotes = any(c.isspace() for c in value) or any(c in value for c in ['#', '"', "'"])
    if not needs_quotes:
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _discover_env_example(start_dir: Path, max_parent_levels: int = 8) -> Optional[Path]:
    """
    Try to find a `.env.example` by walking up parent directories.
    """
    start_dir = start_dir.resolve()
    candidates = [start_dir, *list(start_dir.parents)[:max_parent_levels]]
    for d in candidates:
        p = d / ".env.example"
        if p.exists() and p.is_file():
            return p
    return None


def _split_unquoted_comment(rest: str) -> tuple[str, str]:
    """
    Split "value[ ws ][#comment]" into (value_with_ws, comment) where # is not inside quotes.
    The returned comment includes the leading '#', or '' if not present.
    """
    in_single = False
    in_double = False
    escape = False
    for i, ch in enumerate(rest):
        if escape:
            escape = False
            continue
        if in_double and ch == "\\":
            escape = True
            continue
        if not in_double and ch == "'":
            in_single = not in_single
            continue
        if not in_single and ch == '"':
            in_double = not in_double
            continue
        if ch == "#" and not in_single and not in_double:
            # treat as comment only when it's preceded by whitespace or at start
            if i == 0 or rest[i - 1].isspace():
                return rest[:i], rest[i:]
    return rest, ""


def _format_value_like(existing_value_token: str, new_value: str) -> str:
    """
    Format `new_value` trying to mimic the quoting style of `existing_value_token`.
    Empty value is always written as nothing (KEY=) to avoid KEY="".
    """
    if (new_value or "") == "":
        return ""
    token = (existing_value_token or "").strip()
    if len(token) >= 2 and token[0] == token[-1] and token[0] in ("'", '"'):
        quote = token[0]
        if quote == "'":
            # single quotes can't safely contain single quotes in .env; fall back to default formatter
            if "'" in (new_value or ""):
                return format_env_value(new_value)
            return f"'{new_value}'"
        escaped = (new_value or "").replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return format_env_value(new_value)


@dataclass(frozen=True)
class EnvUpdateResult:
    path: Path
    backup_path: Optional[Path]
    updated_keys: List[str]
    appended_keys: List[str]


def update_env_file(
    path: str | os.PathLike[str],
    updates: Dict[str, str],
    managed_section_title: str = "PowerMem managed section (pmem config init)",
    template_path: str | os.PathLike[str] | None = None,
) -> EnvUpdateResult:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    existing_lines: List[str]
    if target.exists():
        existing_lines = target.read_text(encoding="utf-8").splitlines(keepends=True)
    else:
        template: Optional[Path] = Path(template_path) if template_path else _discover_env_example(Path.cwd())
        if template and template.exists():
            existing_lines = template.read_text(encoding="utf-8").splitlines(keepends=True)
        else:
            existing_lines = []

    backup_path: Optional[Path] = None
    if target.exists():
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_path = target.with_name(f"{target.name}.bak.{ts}")
        backup_path.write_text("".join(existing_lines), encoding="utf-8")

    # Find first occurrence of keys to be updated (capture per-line formatting)
    key_to_index: Dict[str, int] = {}
    key_to_leading: Dict[str, str] = {}
    key_to_prefix: Dict[str, str] = {}
    key_to_sep: Dict[str, str] = {}
    key_to_value_token: Dict[str, str] = {}
    key_to_ws_before_comment: Dict[str, str] = {}
    key_to_comment: Dict[str, str] = {}
    key_to_eol: Dict[str, str] = {}
    for i, line in enumerate(existing_lines):
        m = _ENV_ASSIGN_CAPTURE_RE.match(line)
        if not m:
            continue
        key = m.group("key")
        if key in key_to_index:
            continue
        key_to_index[key] = i
        key_to_leading[key] = m.group("leading") or ""
        key_to_prefix[key] = m.group("export") or ""
        key_to_sep[key] = m.group("sep") or "="
        rest = m.group("rest") or ""
        value_with_ws, comment = _split_unquoted_comment(rest)
        m_ws = re.match(r"^(?P<val>.*?)(?P<ws>\s*)$", value_with_ws, flags=re.DOTALL)
        key_to_value_token[key] = (m_ws.group("val") if m_ws else value_with_ws)
        key_to_ws_before_comment[key] = (m_ws.group("ws") if m_ws else "")
        key_to_comment[key] = comment
        eol = m.group("eol")
        key_to_eol[key] = eol if eol is not None else ("\n" if line.endswith("\n") else "")

    updated_keys: List[str] = []
    appended_keys: List[str] = []

    new_lines = list(existing_lines)
    for key, value in updates.items():
        if key in key_to_index:
            leading = key_to_leading.get(key, "")
            prefix = key_to_prefix.get(key, "")
            sep = key_to_sep.get(key, "=")
            old_token = key_to_value_token.get(key, "")
            ws = key_to_ws_before_comment.get(key, "")
            comment = key_to_comment.get(key, "")
            eol = key_to_eol.get(key, "\n")
            new_val = _format_value_like(old_token, value)
            formatted = f"{leading}{prefix}{key}{sep}{new_val}{ws}{comment}{eol}"
            new_lines[key_to_index[key]] = formatted
            updated_keys.append(key)
        else:
            appended_keys.append(key)

    if appended_keys:
        if new_lines and not new_lines[-1].endswith("\n"):
            new_lines[-1] = new_lines[-1] + "\n"
        if new_lines and new_lines[-1].strip() != "":
            new_lines.append("\n")
        new_lines.append(f"# --- {managed_section_title} ---\n")
        for key in appended_keys:
            new_lines.append(f"{key}={format_env_value(updates[key])}\n")
        new_lines.append("# --- end ---\n")

    target.write_text("".join(new_lines), encoding="utf-8")
    try:
        os.chmod(str(target), 0o600)
    except Exception:
        # Best-effort; don't fail on filesystems that don't support chmod
        pass

    return EnvUpdateResult(
        path=target,
        backup_path=backup_path,
        updated_keys=sorted(updated_keys),
        appended_keys=sorted(appended_keys),
    )


def read_env_file(path: str | os.PathLike[str]) -> Tuple[List[str], Dict[str, str]]:
    p = Path(path)
    if not p.exists():
        return ([], {})
    lines = p.read_text(encoding="utf-8").splitlines(keepends=True)
    return (lines, parse_env_lines(lines))
