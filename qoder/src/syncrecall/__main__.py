"""``python -m syncrecall`` -> CLI entry point.

Requires the package to be importable, i.e. either ``pip install -e .`` from the
``qoder/`` directory or ``PYTHONPATH=src``. ``scripts/`` and ``serve.py`` set up
the path themselves and do not need this.
"""

from syncrecall.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
