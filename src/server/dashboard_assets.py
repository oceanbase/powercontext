"""Shared helpers for locating the bundled Dashboard assets."""

from pathlib import Path


DASHBOARD_DIR = Path(__file__).resolve().parent / "dashboard"


def dashboard_assets_available(directory: Path | None = None) -> bool:
    """Return whether a built Dashboard entrypoint is available."""
    dashboard_dir = directory or DASHBOARD_DIR
    return dashboard_dir.is_dir() and (dashboard_dir / "index.html").is_file()
