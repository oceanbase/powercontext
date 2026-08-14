from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest


def _load_script(name: str) -> ModuleType:
    path = Path(__file__).parents[1] / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


github = _load_script("ci_verify_github_release")
pypi = _load_script("ci_verify_pypi_release")
smoke = _load_script("ci_release_smoke")


@pytest.mark.skipif(smoke.os.name == "nt", reason="POSIX venv symlink regression")
def test_release_smoke_resolves_console_script_from_verification_python(tmp_path) -> None:
    scripts = tmp_path / "verification" / "bin"
    scripts.mkdir(parents=True)
    base_python = tmp_path / "base-python"
    base_python.touch()
    python = scripts / "python"
    python.symlink_to(base_python)
    console_script = scripts / "powercontext"
    console_script.touch()

    assert smoke._console_script(python) == console_script


def test_pypi_release_requires_wheel_and_sdist() -> None:
    metadata = {
        "info": {"version": "1.2.3"},
        "urls": [
            {"packagetype": "bdist_wheel", "filename": "powercontext-1.2.3-py3-none-any.whl"},
            {"packagetype": "sdist", "filename": "powercontext-1.2.3.tar.gz"},
        ],
    }

    assert pypi.validate_release(metadata, "powercontext", "1.2.3") == (
        "powercontext-1.2.3-py3-none-any.whl",
        "powercontext-1.2.3.tar.gz",
    )

    metadata["urls"].pop()
    with pytest.raises(pypi.ReleaseVerificationError, match="missing distribution types: sdist"):
        pypi.validate_release(metadata, "powercontext", "1.2.3")


def test_pypi_verification_retries_until_release_is_complete(monkeypatch) -> None:
    responses = iter([
        {"info": {"version": "1.2.3"}, "urls": []},
        {
            "info": {"version": "1.2.3"},
            "urls": [
                {"packagetype": "bdist_wheel", "filename": "powercontext-1.2.3-py3-none-any.whl"},
                {"packagetype": "sdist", "filename": "powercontext-1.2.3.tar.gz"},
            ],
        },
    ])
    monkeypatch.setattr(pypi.time, "sleep", lambda _: None)

    files = pypi.verify_with_retry(
        "powercontext",
        "1.2.3",
        attempts=2,
        delay_seconds=0,
        fetch=lambda _package, _version: next(responses),
    )

    assert len(files) == 2


def test_github_release_requires_published_state_and_expected_assets() -> None:
    metadata = {
        "tag_name": "v1.2.3",
        "draft": False,
        "prerelease": False,
        "assets": [
            {"name": "powercontext-1.2.3-py3-none-any.whl"},
            {"name": "powercontext-1.2.3.tar.gz"},
        ],
    }
    expected = {"powercontext-1.2.3-py3-none-any.whl", "powercontext-1.2.3.tar.gz"}

    assert set(github.validate_release(metadata, "v1.2.3", expected)) == expected

    metadata["assets"].pop()
    with pytest.raises(github.ReleaseVerificationError, match=r"missing assets: powercontext-1\.2\.3\.tar\.gz"):
        github.validate_release(metadata, "v1.2.3", expected)


def test_github_release_allows_prereleases() -> None:
    metadata = {
        "tag_name": "v1.2.3-rc.1",
        "draft": False,
        "prerelease": True,
        "assets": [
            {"name": "powercontext-1.2.3-rc.1-py3-none-any.whl"},
            {"name": "powercontext-1.2.3-rc.1.tar.gz"},
        ],
    }

    assert github.validate_release(
        metadata,
        "v1.2.3-rc.1",
        {"powercontext-1.2.3-rc.1-py3-none-any.whl", "powercontext-1.2.3-rc.1.tar.gz"},
    )
