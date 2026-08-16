from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GitFixture:
    work: Path
    remote: Path
    initial_sha: str
    feature_sha: str

    def commit_to_feature(self, message: str) -> str:
        git(self.work, "checkout", "feature")
        marker = self.work / "feature.txt"
        marker.write_text(f"{marker.read_text(encoding='utf-8')}{message}\n", encoding="utf-8")
        git(self.work, "add", "feature.txt")
        git(self.work, "commit", "-m", message)
        sha = git(self.work, "rev-parse", "HEAD").stdout.strip()
        git(self.work, "push", "origin", "feature")
        return sha


def git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def create_git_fixture(tmp_path: Path) -> GitFixture:
    work = tmp_path / "source"
    remote = tmp_path / "remote.git"
    work.mkdir()
    remote.mkdir()
    git(work, "init")
    git(work, "symbolic-ref", "HEAD", "refs/heads/main")
    git(work, "config", "user.name", "PowerContext Tests")
    git(work, "config", "user.email", "powercontext-tests@example.invalid")

    (work / "README.md").write_text("initial\n", encoding="utf-8")
    git(work, "add", "README.md")
    git(work, "commit", "-m", "initial")
    initial_sha = git(work, "rev-parse", "HEAD").stdout.strip()
    git(work, "tag", "v1")
    git(work, "tag", "-a", "annotated-v1", "-m", "annotated release")
    git(work, "branch", "release")
    git(work, "checkout", "-b", "feature")
    (work / "feature.txt").write_text("feature\n", encoding="utf-8")
    git(work, "add", "feature.txt")
    git(work, "commit", "-m", "feature")
    feature_sha = git(work, "rev-parse", "HEAD").stdout.strip()
    git(work, "checkout", "main")

    git(remote, "init", "--bare")
    git(remote, "symbolic-ref", "HEAD", "refs/heads/main")
    git(work, "remote", "add", "origin", str(remote))
    git(work, "push", "origin", "main", "feature", "release", "--tags")

    return GitFixture(
        work=work,
        remote=remote,
        initial_sha=initial_sha,
        feature_sha=feature_sha,
    )
