"""Command line entry points for seeding, recall, review and memory inspection.

Examples
--------
    python -m syncrecall seed                 # load data/seed.json into seekdb
    python -m syncrecall recall "Raft 怎么保证不丢已提交日志"
    python -m syncrecall review --course 分布式系统
    python -m syncrecall memory --user demo-user
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from syncrecall.config import SETTINGS  # noqa: E402
from syncrecall.memory import PowerContextMemory  # noqa: E402
from syncrecall.service import Service, seed as seed_store  # noqa: E402
from syncrecall.store import DB  # noqa: E402


def _print_json(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="syncrecall", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_seed = sub.add_parser("seed", help="load data/seed.json into seekdb")
    p_seed.add_argument("--data", default=None)
    p_seed.add_argument("--reset", action="store_true")

    p_recall = sub.add_parser("recall", help="hybrid recall with citations")
    p_recall.add_argument("question")
    p_recall.add_argument("--user", default="demo-user")
    p_recall.add_argument("--limit", type=int, default=None)

    p_review = sub.add_parser("review", help="next review plan")
    p_review.add_argument("--course", default="")
    p_review.add_argument("--user", default="demo-user")

    p_memory = sub.add_parser("memory", help="show the PowerContext memory snapshot")
    p_memory.add_argument("--user", default="demo-user")

    p_health = sub.add_parser("health", help="check seekdb and PowerContext")

    args = parser.parse_args(argv)

    if args.command == "health":
        _print_json(Service().health())
        return 0

    if args.command == "seed":
        db = DB()
        _print_json(seed_store(db, SETTINGS, args.data, reset=args.reset))
        return 0

    service = Service()
    if args.command == "recall":
        result = service.recall(args.user, args.question, limit=args.limit)
        _print_json(result.as_dict())
        return 0
    if args.command == "review":
        _print_json(service.review_plan(args.user, args.course))
        return 0
    if args.command == "memory":
        _print_json(service.get_profile(args.user))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
