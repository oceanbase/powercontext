"""Load the spec dataset into seekdb and index the repository sources it cites.

The spec text is authoritative data: once loaded, the system only reads it back from
the database.  The importer therefore refuses to silently rewrite a spec that already
exists -- callers choose between ``--force`` (rewrite) and nothing (report a conflict).

Usage:
    python scripts/build_specs.py [--force]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from context.db import DB_NAME, connect, ensure_schema  # noqa: E402

SEED = ROOT / "context" / "specs" / "seed_specs.json"
REPO_ENV = "SEEKDB_REPO"


def now() -> datetime:
    return datetime.now()


def resolve_repo(repo: str) -> Path:
    if not repo:
        raise SystemExit(f"set {REPO_ENV} to the seekdb checkout that the specs cite")
    path = Path(repo).resolve()
    if not (path / "src" / "sql" / "parser" / "sql_parser_mysql_mode.y").is_file():
        raise SystemExit(f"{path} does not look like a seekdb checkout")
    return path


def rewrite_seed_from_repo(seed: dict, repo: Path) -> dict:
    """Verify each cited location in the checkout and record a verbatim excerpt.

    The curated ``line`` is kept as written: it was captured against the pinned
    baseline build.  A drifted checkout is reported, not silently renumbered, because
    an older commit legitimately has a different line for the same symbol.
    """
    for spec in seed["specs"]:
        for source in spec.get("sources", []):
            path = repo / source["path"]
            source["available"] = path.is_file()
            if not source["available"]:
                print(f"  ! {spec['id']}: missing source {source['path']}", file=sys.stderr)
                continue
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            symbol = (source.get("symbol") or "").split("(")[0]
            if symbol and not symbol.startswith("ob_expr_*"):
                found = None
                for index, line in enumerate(lines, start=1):
                    if symbol in line:
                        found = index
                        break
                if found is None:
                    print(f"  ! {spec['id']}: symbol {symbol} not found in {source['path']}",
                          file=sys.stderr)
                elif source.get("line") and source["line"] != found:
                    print(f"  ~ {spec['id']}: {source['path']} has {symbol} at line {found}, "
                          f"baseline recorded {source['line']}", file=sys.stderr)
                source["current_line"] = found or source.get("line") or 0
            else:
                source["current_line"] = source.get("line") or 0
            line = source.get("line") or source.get("current_line")
            if line:
                start = max(0, int(line) - 1)
                excerpt = "\n".join(lines[start:start + 4])
                if not source.get("quote"):
                    source["quote"] = excerpt
    return seed

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="rewrite specs that already exist")
    parser.add_argument("--reset", action="store_true", help="delete every spec and source first")
    args = parser.parse_args()

    seed = json.loads(SEED.read_text(encoding="utf-8"))
    repo = resolve_repo(
        __import__("os").environ.get(REPO_ENV, "")
        or "/opt/seekdb"
    )
    print(f"repository: {repo}")
    seed = rewrite_seed_from_repo(seed, repo)

    conn = connect()
    ensure_schema()
    try:
        with conn.cursor() as cur:
            if args.reset:
                cur.execute("DELETE FROM spec_source")
                cur.execute("DELETE FROM test_case")
                cur.execute("DELETE FROM spec")
                print("- cleared spec tables")
            inserted = updated = skipped = 0
            for spec in seed["specs"]:
                cur.execute("SELECT 1 FROM spec WHERE spec_id = %s", (spec["id"],))
                exists = cur.fetchone() is not None
                if exists and not args.force:
                    skipped += 1
                    print(f"  = {spec['id']} already present (use --force to rewrite)")
                    continue
                values = (
                    spec["id"], spec["title"], spec["category"], spec["kind"],
                    spec.get("state", "open"), spec["statement"],
                    json.dumps(spec.get("evidence", {}), ensure_ascii=False),
                    spec.get("evidence", {}).get("repro", ""),
                    ",".join(spec.get("tests", [])),
                    ",".join(spec.get("depends_on", [])),
                    now(), now(),
                )
                cur.execute(
                    """
                    INSERT INTO spec(spec_id, title, category, kind, state, statement, evidence,
                                     repro, tests, depends_on, created_at, updated_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON DUPLICATE KEY UPDATE title=VALUES(title), category=VALUES(category),
                        kind=VALUES(kind), state=VALUES(state), statement=VALUES(statement),
                        evidence=VALUES(evidence), repro=VALUES(repro), tests=VALUES(tests),
                        depends_on=VALUES(depends_on), updated_at=VALUES(updated_at)
                    """,
                    values,
                )
                cur.execute("DELETE FROM spec_source WHERE spec_id = %s", (spec["id"],))
                for source in spec.get("sources", []):
                    cur.execute(
                        """
                        INSERT INTO spec_source(spec_id, repo_path, symbol, line_no, quote,
                                                note, captured_at)
                        VALUES (%s,%s,%s,%s,%s,%s,%s)
                        """,
                        (
                            spec["id"], source["path"], source.get("symbol", ""),
                            int(source.get("line") or 0), source.get("quote", ""),
                            source.get("note", ""), now(),
                        ),
                    )
                inserted += 0 if exists else 1
                updated += 1 if exists else 0
                print(f"  + {spec['id']} {spec['title']}")

            # Every check name referenced by a spec must exist as a runnable case.
            cur.execute("SELECT spec_id, tests FROM spec")
            check_to_specs: dict[str, list[str]] = {}
            for spec_id, tests in cur.fetchall():
                for check in (tests or "").split(","):
                    if check:
                        check_to_specs.setdefault(check, []).append(spec_id)
            cur.execute("DELETE FROM test_case")
            for check, spec_ids in sorted(check_to_specs.items()):
                cur.execute(
                    """
                    INSERT INTO test_case(case_id, spec_id, check_name, title, oracle_key,
                                          created_at)
                    VALUES (%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        f"{check}:{spec_ids[0]}", spec_ids[0], check,
                        CASES.get(check, {}).get("title", check),
                        CASES.get(check, {}).get("oracle", "golden"),
                        now(),
                    ),
                )
            print(f"  + {len(check_to_specs)} checks registered")
    finally:
        conn.close()

    checksum = hashlib.sha256(SEED.read_bytes()).hexdigest()
    print(
        f"done: inserted={inserted} updated={updated} skipped={skipped} "
        f"dataset_sha256={checksum[:16]} database={DB_NAME}"
    )
    return 0


CASES = {
    "parser_only": {"title": "Grammar accepts the element-access form", "oracle": "parse"},
    "live_server": {"title": "Statement runs on a live seekdb", "oracle": "golden"},
}


if __name__ == "__main__":
    sys.exit(main())
