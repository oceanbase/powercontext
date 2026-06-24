"""
Test create_env_file() db_provider and embedding_provider logic from init.sh.
No powermem package needed — pure stdlib.

Run with: python apps/claude-code-plugin/tests/test_env_generation.py
"""
import os
import sys
from pathlib import Path


def env_first(*names):
    for name in names:
        value = os.environ.get(name)
        if value is not None and value.strip():
            return value.strip()
    return ""


def path_value(data_dir, *parts):
    return str(Path(data_dir).joinpath(*parts))


def build_env(data_dir):
    """Replicate the db_provider + embedding_provider logic from init.sh."""
    VALID_PROVIDERS = {"sqlite", "oceanbase"}
    raw = env_first("POWERMEM_INIT_DATABASE_PROVIDER") or "sqlite"
    db_provider = raw.lower()
    db_warn = False
    if db_provider not in VALID_PROVIDERS:
        db_provider = "sqlite"
        db_warn = True

    _embedding_fallback = "huggingface" if db_provider == "sqlite" else "default"
    embedding_provider = (
        env_first("POWERMEM_INIT_EMBEDDING_PROVIDER", "EMBEDDING_PROVIDER")
        or _embedding_fallback
    ).lower()

    if db_provider == "sqlite":
        db_lines = [
            "DATABASE_PROVIDER=sqlite",
            f"SQLITE_PATH={path_value(data_dir, 'powermem.db')}",
        ]
    else:
        db_lines = [
            "DATABASE_PROVIDER=oceanbase",
            "OCEANBASE_HOST=",
        ]

    return db_provider, embedding_provider, db_lines, db_warn


def run_case(label, env_overrides, expected_db, expected_emb, expect_warn=False):
    old = os.environ.copy()
    for k in list(os.environ):
        os.environ.pop(k, None)
    for k, v in env_overrides.items():
        os.environ[k] = v

    db_prov, emb_prov, lines, warned = build_env("/tmp/test-data")

    os.environ.clear()
    os.environ.update(old)

    ok = db_prov == expected_db and emb_prov == expected_emb and warned == expect_warn
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {label}")
    print(f"       db_provider={db_prov!r}, embedding_provider={emb_prov!r}, warned={warned}")
    if not ok:
        print(f"       EXPECTED db={expected_db!r}, emb={expected_emb!r}, warn={expect_warn}")
    print()
    return ok


if __name__ == "__main__":
    results = []

    # ── db_provider branching ──────────────────────────────────────────────────
    results.append(run_case(
        "db: no env → sqlite+huggingface", {}, "sqlite", "huggingface"))
    results.append(run_case(
        "db: explicit sqlite", {"POWERMEM_INIT_DATABASE_PROVIDER": "sqlite"}, "sqlite", "huggingface"))
    results.append(run_case(
        "db: SQLITE uppercase", {"POWERMEM_INIT_DATABASE_PROVIDER": "SQLITE"}, "sqlite", "huggingface"))
    results.append(run_case(
        "db: oceanbase → default embedder", {"POWERMEM_INIT_DATABASE_PROVIDER": "oceanbase"}, "oceanbase", "default"))
    results.append(run_case(
        "db: invalid → fallback sqlite", {"POWERMEM_INIT_DATABASE_PROVIDER": "badvalue"}, "sqlite", "huggingface",
        expect_warn=True))

    # ── user embedding override respected ─────────────────────────────────────
    results.append(run_case(
        "emb: sqlite+override qwen", {"POWERMEM_INIT_EMBEDDING_PROVIDER": "qwen"}, "sqlite", "qwen"))
    results.append(run_case(
        "emb: sqlite+override openai", {"POWERMEM_INIT_EMBEDDING_PROVIDER": "openai"}, "sqlite", "openai"))
    results.append(run_case(
        "emb: sqlite+override default (user-forced)", {"POWERMEM_INIT_EMBEDDING_PROVIDER": "default"},
        "sqlite", "default"))
    results.append(run_case(
        "emb: oceanbase+override huggingface", {
            "POWERMEM_INIT_DATABASE_PROVIDER": "oceanbase",
            "POWERMEM_INIT_EMBEDDING_PROVIDER": "huggingface",
        }, "oceanbase", "huggingface"))

    passed = sum(results)
    total = len(results)
    print("=" * 40)
    print(f"Result: {passed}/{total} passed")
    sys.exit(0 if passed == total else 1)
