"""Where the package lives, and a helper to bootstrap ``sys.path``.

The application runs from a source checkout without installation, so entry points
call :func:`bootstrap` before importing the package modules.
"""

from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent
PROJECT = SRC.parent


def bootstrap() -> Path:
    """Make ``import syncrecall`` work from ``scripts/`` and ``serve.py``."""
    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))
    return PROJECT
