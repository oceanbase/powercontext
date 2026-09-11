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

"""Observable behavior of the bilingual configuration wizard's terminal UI."""

from __future__ import annotations

from collections.abc import Callable
from subprocess import CompletedProcess
from types import SimpleNamespace

import pytest
import typer
from typer.testing import CliRunner

import powercontext.cli.config_wizard_ui as wizard_ui


class _Prompt:
    def __init__(self, answer):
        self.answer = answer

    def execute(self):
        return self.answer


class _FakeInquirer:
    def __init__(self) -> None:
        self.answers = []
        self.calls = []

    def _prompt(self, kind: str, **kwargs):
        self.calls.append((kind, kwargs))
        return _Prompt(self.answers.pop(0))

    def select(self, **kwargs):
        return self._prompt("select", **kwargs)

    def fuzzy(self, **kwargs):
        return self._prompt("fuzzy", **kwargs)

    def text(self, **kwargs):
        return self._prompt("text", **kwargs)

    def secret(self, **kwargs):
        return self._prompt("secret", **kwargs)

    def confirm(self, **kwargs):
        return self._prompt("confirm", **kwargs)


@pytest.fixture
def fake_inquirer(monkeypatch):
    fake = _FakeInquirer()
    monkeypatch.setitem(wizard_ui.sys.modules, "InquirerPy", SimpleNamespace(inquirer=fake))
    return fake


def _invoke(action: Callable[[], None], input_text: str = ""):
    app = typer.Typer()
    app.command()(action)
    return CliRunner().invoke(app, [], input=input_text)


@pytest.fixture
def empty_locale(monkeypatch):
    for name in ("LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(wizard_ui.locale, "getlocale", lambda *_args: (None, None))
    monkeypatch.setattr(wizard_ui.sys, "platform", "linux")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("zh_CN.UTF-8", "zh"),
        ("zh-TW", "zh"),
        ("ZH_Hant_HK", "zh"),
        ("en_US.UTF-8", "en"),
        ("en-GB", "en"),
        ("fr_FR.UTF-8", "en"),
        ("C", "en"),
        ("POSIX", "en"),
        ("C.UTF-8", "en"),
        ("", "en"),
        ("unknown", "en"),
    ],
)
def test_normalize_supported_languages_and_fallback(value: str, expected: str) -> None:
    assert wizard_ui.normalize_language(value) == expected


@pytest.mark.usefixtures("empty_locale")
@pytest.mark.parametrize("selected", ["fr_FR.UTF-8", "C", "POSIX", "C.UTF-8"])
def test_first_locale_wins_even_when_lower_priority_language_is_supported(monkeypatch, selected: str) -> None:
    monkeypatch.setenv("LC_ALL", selected)
    monkeypatch.setenv("LC_MESSAGES", "zh_TW.UTF-8")
    monkeypatch.setenv("LANG", "zh_CN.UTF-8")
    assert wizard_ui.detect_language() == "en"


@pytest.mark.usefixtures("empty_locale")
def test_messages_locale_takes_precedence_over_lang(monkeypatch) -> None:
    monkeypatch.setenv("LC_MESSAGES", "zh_TW.UTF-8")
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    assert wizard_ui.detect_language() == "zh"


@pytest.mark.usefixtures("empty_locale")
@pytest.mark.parametrize("value", ["zh_CN.UTF-8", "zh-TW"])
def test_detect_language_uses_lang(monkeypatch, value: str) -> None:
    monkeypatch.setenv("LANG", value)
    assert wizard_ui.detect_language() == "zh"


@pytest.mark.usefixtures("empty_locale")
def test_missing_locale_defaults_to_english() -> None:
    assert wizard_ui.detect_language() == "en"


@pytest.mark.usefixtures("empty_locale")
@pytest.mark.parametrize(
    ("preferences", "expected"),
    [('(\n    "zh-Hans-CN",\n    "en-US"\n)', "zh"), ('(\n    "fr-FR",\n    "zh-Hans"\n)', "en")],
)
def test_macos_uses_first_preferred_language_only_when_locale_is_absent(monkeypatch, preferences, expected) -> None:
    monkeypatch.setattr(wizard_ui.sys, "platform", "darwin")
    monkeypatch.setattr(
        wizard_ui.subprocess,
        "run",
        lambda *_args, **_kwargs: CompletedProcess([], 0, stdout=preferences, stderr=""),
    )
    assert wizard_ui.detect_language() == expected
    monkeypatch.setenv("LANG", "C")
    assert wizard_ui.detect_language() == "en"


@pytest.mark.usefixtures("empty_locale")
def test_macos_preference_failure_falls_back_to_english(monkeypatch) -> None:
    monkeypatch.setattr(wizard_ui.sys, "platform", "darwin")

    def unavailable(*_args, **_kwargs):
        raise OSError

    monkeypatch.setattr(wizard_ui.subprocess, "run", unavailable)
    assert wizard_ui.detect_language() == "en"


@pytest.mark.parametrize("selected", ["en", "zh"])
def test_explicit_language_skips_the_menu(selected: str) -> None:
    observed = []

    def action() -> None:
        observed.append(wizard_ui.choose_language(explicit=selected))

    result = _invoke(action)
    assert result.exit_code == 0
    assert result.output == ""
    assert observed == [selected]


@pytest.mark.parametrize(("previous", "expected"), [(None, "zh"), ("en", "en")])
def test_language_menu_is_always_bilingual_and_preserves_preferred_default(monkeypatch, previous, expected) -> None:
    monkeypatch.setattr(wizard_ui, "detect_language", lambda: "zh")
    observed = []

    def action() -> None:
        observed.append(wizard_ui.choose_language(previous=previous))

    result = _invoke(action, "\n")
    assert result.exit_code == 0
    assert "Language / 语言" in result.output
    assert "English" in result.output
    assert "中文" in result.output
    assert observed == [expected]


@pytest.mark.parametrize("entered", ["2", "zh"])
def test_language_menu_supports_numbers_and_stable_identifiers(monkeypatch, entered: str) -> None:
    monkeypatch.setattr(wizard_ui, "detect_language", lambda: "en")
    observed = []

    def action() -> None:
        observed.append(wizard_ui.choose_language())

    result = _invoke(action, f"{entered}\n")
    assert result.exit_code == 0
    assert observed == ["zh"]


def test_chinese_menu_retries_invalid_selection_and_returns_identifier() -> None:
    ui = wizard_ui.WizardUI("zh")
    observed = []

    def action() -> None:
        observed.append(ui.choose("Storage", "存储", [("sqlite", "Local", "本地")], "sqlite"))

    result = _invoke(action, "9\ninvalid\n1\n")
    assert result.exit_code == 0
    assert "本地" in result.output
    assert "请输入" in result.output
    assert "Invalid" not in result.output
    assert observed == ["sqlite"]


@pytest.mark.parametrize(("entered", "expected"), [("", "saved-credential"), ("new-credential", "new-credential")])
def test_secret_input_hides_existing_and_typed_values(entered: str, expected: str) -> None:
    observed = []

    def action() -> None:
        observed.append(wizard_ui.WizardUI("en").ask("API key", "密钥", "saved-credential", secret=True))

    result = _invoke(action, f"{entered}\n")
    assert result.exit_code == 0
    assert "saved-credential" not in result.output
    assert "new-credential" not in result.output
    assert observed == [expected]


def test_required_value_and_integer_retry_in_selected_language() -> None:
    ui = wizard_ui.WizardUI("zh")
    observed = []

    def action() -> None:
        observed.append(ui.ask("Name", "名称", required=True))
        observed.append(ui.integer("Port", "端口", default=8000, minimum=1, maximum=65535))

    result = _invoke(action, "\n  alice  \nabc\n0\n65536\n9000\n")
    assert result.exit_code == 0
    assert "不能为空" in result.output
    assert "整数" in result.output
    assert observed == ["alice", 9000]


def test_redirected_activity_wait_is_quiet_and_waits_until_done() -> None:
    ui = wizard_ui.WizardUI("zh", interactive=False)
    checks = iter((False, False, True))
    sleeps: list[float] = []

    ui.wait_for_activity(
        lambda: next(checks),
        lambda: "installing",
        sleep=sleeps.append,
        interval=0.01,
    )

    assert sleeps == [0.01, 0.01]


@pytest.mark.parametrize(("entered", "expected"), [("是", True), ("否", False), ("y", True), ("n", False), ("", True)])
def test_chinese_confirmation_accepts_local_and_english_answers(entered, expected) -> None:
    observed = []

    def action() -> None:
        observed.append(wizard_ui.WizardUI("zh").confirm("Continue?", "继续？"))  # noqa: RUF001

    result = _invoke(action, f"{entered}\n")
    assert result.exit_code == 0
    assert observed == [expected]


def test_interactive_choice_uses_localized_select(fake_inquirer) -> None:
    fake_inquirer.answers = ["sqlite"]
    ui = wizard_ui.WizardUI("zh", interactive=True)

    assert ui.choose("Storage", "存储", [("sqlite", "Local", "本地")], "sqlite") == "sqlite"
    kind, options = fake_inquirer.calls[0]
    assert kind == "select"
    assert options == {
        "message": "存储",
        "choices": [{"name": "本地", "value": "sqlite"}],
        "default": "sqlite",
    }


def test_interactive_search_uses_fuzzy_with_localized_instruction(fake_inquirer) -> None:
    fake_inquirer.answers = ["bailian"]
    ui = wizard_ui.WizardUI("zh", interactive=True)

    choices = [("openai", "OpenAI", "OpenAI"), ("bailian", "Bailian", "阿里云百炼")]
    assert ui.search("Provider", "服务商", choices, "bailian") == "bailian"
    kind, options = fake_inquirer.calls[0]
    assert kind == "fuzzy"
    assert options["message"] == "服务商"
    assert options["choices"] == [
        {"name": "阿里云百炼", "value": "bailian"},
        {"name": "OpenAI", "value": "openai"},
    ]
    assert "default" not in options
    assert options["instruction"] == "（输入可搜索）"  # noqa: RUF001


def test_interactive_search_retries_when_no_choice_matches(fake_inquirer) -> None:
    fake_inquirer.answers = [None, "bailian"]
    ui = wizard_ui.WizardUI("en", interactive=True)

    assert ui.search("Provider", "服务商", [("bailian", "Bailian", "百炼")], "bailian") == "bailian"
    assert [kind for kind, _ in fake_inquirer.calls] == ["fuzzy", "fuzzy"]


def test_interactive_text_shows_default_as_hint_without_prefilling_input(fake_inquirer) -> None:
    fake_inquirer.answers = ["9000"]
    ui = wizard_ui.WizardUI("zh", interactive=True)

    assert ui.ask("Port", "端口", default="8000") == "9000"
    assert fake_inquirer.calls == [("text", {"message": "端口 [8000]"})]


def test_interactive_text_uses_default_when_input_is_empty(fake_inquirer) -> None:
    fake_inquirer.answers = [""]
    ui = wizard_ui.WizardUI("en", interactive=True)

    assert ui.ask("Port", "端口", default="8000") == "8000"


def test_interactive_secret_preserves_empty_retained_value_without_exposing_it(fake_inquirer) -> None:
    fake_inquirer.answers = [""]
    ui = wizard_ui.WizardUI("en", interactive=True)

    assert ui.ask("API key", "密钥", default="saved-credential", secret=True) == "saved-credential"
    assert fake_inquirer.calls == [("secret", {"message": "API key"})]
    assert "saved-credential" not in repr(fake_inquirer.calls)


def test_interactive_confirm_uses_native_boolean_prompt(fake_inquirer) -> None:
    fake_inquirer.answers = [False]
    ui = wizard_ui.WizardUI("zh", interactive=True)

    assert ui.confirm("Continue?", "继续？", default=True) is False  # noqa: RUF001
    assert fake_inquirer.calls == [("confirm", {"message": "继续？", "default": True})]  # noqa: RUF001


def test_interactive_integer_retries_with_localized_validation(fake_inquirer, capsys) -> None:
    fake_inquirer.answers = ["abc", "0", "9000"]
    ui = wizard_ui.WizardUI("zh", interactive=True)

    assert ui.integer("Port", "端口", default=8000, minimum=1, maximum=65535) == 9000
    assert [kind for kind, _options in fake_inquirer.calls] == ["text", "text", "text"]
    output = capsys.readouterr().out
    assert "请输入整数" in output
    assert "1 到 65535" in output
