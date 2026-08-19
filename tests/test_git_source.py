from __future__ import annotations

import pytest

from powercontext.cli.git_source import InvalidGitHubSourceError, github_clone_url


def test_github_clone_url_rejects_unencrypted_http_source() -> None:
    with pytest.raises(InvalidGitHubSourceError):
        github_clone_url("http://github.com/oceanbase/powercontext")
