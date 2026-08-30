# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

import os

import pytest

from powercontext.cli.env_file import (
    EnvironmentFileError,
    environment_context,
    parse_environment,
    read_environment_file,
)


def test_hash_inside_a_value_is_preserved() -> None:
    assert parse_environment("TOKEN=abc#123\n") == {"TOKEN": "abc#123"}


def test_comment_after_an_assignment_is_removed() -> None:
    assert parse_environment("TOKEN=abc # trailing comment\n") == {"TOKEN": "abc"}


def test_quoted_values_keep_hashes_and_spaces() -> None:
    content = 'TOKEN="#not a comment"\nOTHER=plain#tag\n'
    assert parse_environment(content) == {"TOKEN": "#not a comment", "OTHER": "plain#tag"}


def test_url_fragment_assignment_survives() -> None:
    assert parse_environment("URL=https://example.com/page#section\n") == {"URL": "https://example.com/page#section"}


def test_export_prefix_keeps_hash_values() -> None:
    assert parse_environment("export BEARER=token#a1\n") == {"BEARER": "token#a1"}


def test_full_line_comments_are_ignored() -> None:
    content = "# leading comment\n\nTOKEN=value # explanation\n"
    assert parse_environment(content) == {"TOKEN": "value"}


def test_value_may_start_with_hash_like_shell() -> None:
    assert parse_environment("TOKEN=#literal\n") == {"TOKEN": "#literal"}


def test_backslash_escaped_space_preserves_hash_value() -> None:
    assert parse_environment("TOKEN=abc\\ #123\n") == {"TOKEN": "abc #123"}


def test_even_backslash_before_space_starts_a_comment_like_shell() -> None:
    assert parse_environment("TOKEN=abc\\\\ #comment\n") == {"TOKEN": "abc\\"}


def test_backslash_escaped_quote_outside_quotes_is_literal() -> None:
    assert parse_environment('TOKEN=abc\\" #comment\n') == {"TOKEN": 'abc"'}


def test_backslash_escaped_tab_preserves_hash_value() -> None:
    assert parse_environment("TOKEN=abc\\\t#123\n") == {"TOKEN": "abc\t#123"}


def test_unescaped_tab_starts_a_comment_boundary() -> None:
    assert parse_environment("TOKEN=abc\t#comment\n") == {"TOKEN": "abc"}


@pytest.mark.parametrize(("whitespace", "expected"), [(" ", "abc "), ("\t", "abc\t")])
def test_trailing_escaped_whitespace_is_preserved(whitespace: str, expected: str) -> None:
    assert parse_environment(f"TOKEN=abc\\{whitespace}\n") == {"TOKEN": expected}


def test_export_accepts_shell_whitespace() -> None:
    assert parse_environment("export\tTOKEN=abc\n") == {"TOKEN": "abc"}


@pytest.mark.parametrize("value", ["$ROOT/data", "${ROOT}/data", "$(command)", "`command`", "~/data"])
def test_shell_expansion_is_rejected_instead_of_loaded_literally(value: str) -> None:
    with pytest.raises(EnvironmentFileError, match="expansion"):
        parse_environment(f"TOKEN={value}\n")


def test_quoted_and_escaped_expansion_characters_are_literal() -> None:
    assert parse_environment("FIRST='$ROOT/data'\nSECOND=\\$ROOT/data\nTHIRD='~/data'\n") == {
        "FIRST": "$ROOT/data",
        "SECOND": "$ROOT/data",
        "THIRD": "~/data",
    }


def test_invalid_utf8_is_reported_as_an_environment_file_error(tmp_path) -> None:
    environment = tmp_path / ".env"
    environment.write_bytes(b"TOKEN=\xff\n")

    with pytest.raises(EnvironmentFileError, match="UTF-8"):
        read_environment_file(environment)


def test_environment_context_can_clear_stale_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POWERCONTEXT_SERVER_AUTH_ENABLED", "true")
    monkeypatch.setenv("POWERCONTEXT_SERVER_HTTP_HOST", "192.0.2.10")

    with environment_context(
        {"POWERCONTEXT_SERVER_HTTP_HOST": "127.0.0.1"},
        override=True,
        clear={"POWERCONTEXT_SERVER_AUTH_ENABLED", "POWERCONTEXT_SERVER_HTTP_HOST"},
    ):
        assert "POWERCONTEXT_SERVER_AUTH_ENABLED" not in os.environ
        assert os.environ["POWERCONTEXT_SERVER_HTTP_HOST"] == "127.0.0.1"

    assert os.environ["POWERCONTEXT_SERVER_AUTH_ENABLED"] == "true"
    assert os.environ["POWERCONTEXT_SERVER_HTTP_HOST"] == "192.0.2.10"


def test_unterminated_quote_is_rejected() -> None:
    with pytest.raises(EnvironmentFileError, match="invalid assignment"):
        parse_environment('TOKEN="unterminated\n')
