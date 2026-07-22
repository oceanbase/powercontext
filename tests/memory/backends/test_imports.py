from __future__ import annotations

import subprocess
import sys

import pytest


def test_database_backends_share_extension_spi() -> None:
    from powercontext.memory.backends import DatabaseMemoryBackend, OceanBaseMemoryBackend, SQLiteMemoryBackend

    assert issubclass(SQLiteMemoryBackend, DatabaseMemoryBackend)
    assert issubclass(OceanBaseMemoryBackend, DatabaseMemoryBackend)


def test_database_backend_spi_does_not_require_a_driver_extra() -> None:
    program = """
import importlib.abc
import sys

class Blocker(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname in {'apsw', 'pymysql'}:
            raise ModuleNotFoundError(fullname)
        return None

sys.meta_path.insert(0, Blocker())
from powercontext.memory.backends import DatabaseMemoryBackend
assert DatabaseMemoryBackend.__name__ == 'DatabaseMemoryBackend'
"""
    completed = subprocess.run(  # noqa: S603
        [sys.executable, "-c", program],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize(
    ("blocked", "module", "backend"),
    [
        ("apsw", "powercontext.memory.backends", "OceanBaseMemoryBackend"),
        ("pymysql", "powercontext.memory.backends", "SQLiteMemoryBackend"),
    ],
)
def test_backend_extras_do_not_import_each_other(
    blocked: str,
    module: str,
    backend: str,
) -> None:
    program = f"""
import importlib.abc
import sys

class Blocker(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname == {blocked!r} or fullname.startswith({blocked!r} + '.'):
            raise ModuleNotFoundError(fullname)
        return None

sys.meta_path.insert(0, Blocker())
loaded = getattr(__import__({module!r}, fromlist=[{backend!r}]), {backend!r})
assert loaded.__name__ == {backend!r}
"""
    completed = subprocess.run(  # noqa: S603
        [sys.executable, "-c", program],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
