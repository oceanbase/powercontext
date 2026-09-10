"""Create reviewable source archives; official CLI validation produces .difypkg files."""

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT.parents[1] / "dist" / "dify"


def package_sources() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for name in ("powercontext", "powercontext_agent"):
        package = ROOT / name
        target = OUTPUT / f"{name}-0.0.1-source.zip"
        with ZipFile(target, "w", compression=ZIP_DEFLATED) as archive:
            for path in sorted(package.rglob("*")):
                if path.is_file() and (
                    path.suffix in {".py", ".yaml", ".svg", ".md", ".txt"} or path.name in {"LICENSE", ".difyignore"}
                ):
                    archive.write(path, f"{name}/{path.relative_to(package)}")
        print(target)


if __name__ == "__main__":
    package_sources()
