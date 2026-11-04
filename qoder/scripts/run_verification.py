"""Run the documented verification flow and write the outputs into ``logs/``.

Each step is one command that a reviewer can copy; the captured stdout/stderr goes
to ``logs/NN-name.log`` so the material ships with the repository.
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
LOGS = ROOT / "logs"
LOGS.mkdir(exist_ok=True)

env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONPATH": str(ROOT / "src")}

STEPS = [
    ("01-seekdb-version", ["docker", "exec", "seekdb-kb", "bash", "-lc",
                           "mysql -h127.0.0.1 -P2881 -uroot -e 'SELECT VERSION();'"], "."),
    ("02-health", [sys.executable, "-m", "syncrecall", "health"], str(ROOT)),
    ("03-seed", [sys.executable, "-m", "syncrecall", "seed", "--reset"], str(ROOT)),
    ("04-indexes", ["mysql", "-h127.0.0.1", "-P2881", "-uroot", "--protocol=TCP",
                    "--default-character-set=utf8mb4", "-e", "SHOW INDEX FROM syncrecall.note;"], "."),
    ("05-pytest", [sys.executable, "-m", "pytest", "tests", "-q"], str(ROOT)),
    ("06-recall-before", [sys.executable, str(ROOT / "scripts" / "demo_recall.py"),
                          "Raft 怎么保证不丢已提交的日志"], "."),
    ("07-explain-plan", [sys.executable, str(ROOT / "scripts" / "explain_plan.py"),
                         "Raft 怎么保证不丢已提交的日志"], "."),
    ("08-evidence", [sys.executable, str(ROOT / "scripts" / "collect_evidence.py")], "."),
]


def main() -> int:
    header = [f"# verification run {datetime.now():%Y-%m-%d %H:%M:%S}", ""]
    for name, command, cwd in STEPS:
        printable = " ".join(command)
        print(f"== {name}: {printable}", flush=True)
        try:
            completed = subprocess.run(
                command,
                cwd=cwd,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=600,
            )
            output = (completed.stdout or "") + (("\n[stderr]\n" + completed.stderr) if completed.stderr else "")
            status = completed.returncode
        except FileNotFoundError as error:
            output = f"command not found: {error}"
            status = 127
        (LOGS / f"{name}.log").write_text(
            f"$ {printable}\n(cwd: {cwd})\nexit: {status}\n\n{output}\n", encoding="utf-8"
        )
        header.append(f"- {name}: exit {status}")
        print(f"   exit {status}, {len(output)} chars", flush=True)
    (LOGS / "00-index.md").write_text("\n".join(header) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
