"""Argument parsing helpers for PowerMem MCP command-line entry points."""

from __future__ import annotations

import argparse


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="powermem-mcp",
        description="Start the PowerMem MCP server.",
    )
    parser.add_argument(
        "transport",
        nargs="?",
        default="streamable-http",
        help="MCP transport to use: streamable-http, sse, or stdio.",
    )
    parser.add_argument(
        "port",
        nargs="?",
        default="8848",
        help="Port for streamable-http or sse transports (default: 8848).",
    )
    return parser
