"""Lightweight command-line entry point for ``powermem-mcp``."""

from typing import List, Optional

from powermem.mcp.args import build_arg_parser


def main(argv: Optional[List[str]] = None) -> None:
    build_arg_parser().parse_args(argv)

    from powermem.mcp.server import main as server_main

    server_main(argv)
