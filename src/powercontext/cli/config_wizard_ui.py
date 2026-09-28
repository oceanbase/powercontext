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

"""Bilingual terminal prompts for the guided environment-file configuration."""

from __future__ import annotations

import locale
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from typing import Any

import typer


def normalize_language(value: str) -> str:
    """Map English and Chinese locale variants to their supported UI language."""
    language = re.split(r"[-_.@]", value.strip().lower(), maxsplit=1)[0]
    return "zh" if language == "zh" else "en"


def _macos_language() -> str | None:
    """Read macOS's first preferred language without changing system settings."""
    try:
        result = subprocess.run(
            ["/usr/bin/defaults", "read", "-g", "AppleLanguages"],
            capture_output=True,
            text=True,
            check=False,
            timeout=1,
        )
    except (OSError, subprocess.TimeoutExpired, UnicodeError):
        return None
    if result.returncode != 0:
        return None
    first = result.stdout.strip().strip("()").strip().split(",", maxsplit=1)[0].strip().strip('"')
    return first or None


def detect_language() -> str:
    """Use the first system locale, falling back to English when unsupported.

    Locale precedence is LC_ALL, LC_MESSAGES, then LANG. In particular, an
    explicit unsupported locale (including C/POSIX) must not fall through to a
    different lower-priority language. macOS preferences are consulted only
    when these environment variables are all absent or empty.
    """
    for name in ("LC_ALL", "LC_MESSAGES", "LANG"):
        if value := os.environ.get(name, "").strip():
            return normalize_language(value)
    if sys.platform == "darwin" and (value := _macos_language()):
        return normalize_language(value)
    try:
        value = locale.getlocale()[0]
    except (ValueError, locale.Error):
        value = None
    return normalize_language(value or "")


def choose_language(explicit: str | None = None, previous: str | None = None) -> str:
    """Offer a bilingual language menu unless the CLI explicitly selected one."""
    if explicit is not None:
        return normalize_language(explicit)
    default = normalize_language(previous) if previous is not None else detect_language()
    return WizardUI(default).choose(
        "Language / 语言",
        "Language / 语言",
        [("en", "English", "English"), ("zh", "中文", "中文")],
        default=default,
    )


class WizardUI:
    """Bilingual InquirerPy UI with a deterministic redirected-input fallback."""

    def __init__(self, language: str, *, interactive: bool | None = None) -> None:
        self.language = normalize_language(language)
        self.interactive = sys.stdin.isatty() and sys.stdout.isatty() if interactive is None else interactive
        self._fallback_reported = False

    def text(self, en: str, zh: str) -> str:
        """Select text without mutating the process locale."""
        return zh if self.language == "zh" else en

    def say(self, en: str, zh: str) -> None:
        """Print an ordinary localized line."""
        typer.echo(self.text(en, zh))

    def section(self, en: str, zh: str) -> None:
        """Print a section heading."""
        typer.echo()
        typer.secho(self.text(en, zh), bold=True, fg=typer.colors.CYAN)

    def wait_for_activity(
        self,
        done: Callable[[], bool],
        phase: Callable[[], str],
        elapsed: Callable[[], float] | None = None,
        *,
        sleep: Callable[[float], None] = time.sleep,
        interval: float = 0.1,
    ) -> None:
        """Wait without escape sequences for redirected input, or animate one TTY line."""

        frames = ("━╺━━━━━━━━━━━━━━━━━━", "━━━━╺━━━━━━━━━━━━━━━", "━━━━━━━━╺━━━━━━━━━━━", "━━━━━━━━━━━━╺━━━━━━━")
        frame = 0
        while not done():
            if self.interactive:
                seconds = 0 if elapsed is None else int(elapsed())
                label = _activity_phase(self.language, phase())
                typer.echo(f"\r{frames[frame % len(frames)]}  {label} · {seconds}s", nl=False)
                frame += 1
            sleep(interval)
        if self.interactive:
            typer.echo("\r" + " " * 72 + "\r", nl=False)

    def choose(self, en: str, zh: str, choices: Sequence[tuple[str, str, str]], default: str) -> str:
        """Return a stable choice identifier from a numeric or identifier answer."""
        identifiers = [identifier for identifier, _, _ in choices]
        if not identifiers or default not in identifiers:
            message = "Wizard choice default must identify one of its choices."
            raise ValueError(message)
        inquirer = self._inquirer()
        if inquirer is not None:
            return str(
                inquirer.select(
                    message=self.text(en, zh),
                    choices=[
                        {"name": self.text(en_label, zh_label), "value": identifier}
                        for identifier, en_label, zh_label in choices
                    ],
                    default=default,
                ).execute()
            )
        return self._text_choose(en, zh, choices, default)

    def search(self, en: str, zh: str, choices: Sequence[tuple[str, str, str]], default: str) -> str:
        """Select from a searchable list in a TTY and a numbered list otherwise."""
        identifiers = [identifier for identifier, _, _ in choices]
        if not identifiers or default not in identifiers:
            message = "Wizard choice default must identify one of its choices."
            raise ValueError(message)
        inquirer = self._inquirer()
        if inquirer is not None:
            ordered_choices = sorted(choices, key=lambda choice: choice[0] != default)
            while True:
                selected = inquirer.fuzzy(
                    message=self.text(en, zh),
                    choices=[
                        {"name": self.text(en_label, zh_label), "value": identifier}
                        for identifier, en_label, zh_label in ordered_choices
                    ],
                    instruction=self.text("(type to search)", "（输入可搜索）"),  # noqa: RUF001
                ).execute()
                if selected in identifiers:
                    return str(selected)
                self.say("Choose one of the matching options.", "请从匹配的选项中选择一项。")
        return self._text_choose(en, zh, choices, default)

    def _inquirer(self) -> Any | None:
        if not self.interactive:
            return None
        try:
            from InquirerPy import inquirer
        except ImportError:
            self.interactive = False
            if not self._fallback_reported:
                self.say(
                    "Interactive UI is unavailable; using text prompts.",
                    "交互式界面不可用，改用文本输入。",  # noqa: RUF001
                )
                self._fallback_reported = True
            return None
        return inquirer

    def _text_choose(self, en: str, zh: str, choices: Sequence[tuple[str, str, str]], default: str) -> str:
        identifiers = [identifier for identifier, _, _ in choices]
        self.say(en, zh)
        for number, (_, en_label, zh_label) in enumerate(choices, start=1):
            typer.echo(f"  {number}. {self.text(en_label, zh_label)}")
        default_number = str(identifiers.index(default) + 1)
        while True:
            answer = self.ask("Choose", "请选择", default=default_number)
            for number, identifier in enumerate(identifiers, start=1):
                if answer.casefold() in {str(number), identifier.casefold()}:
                    return identifier
            self.say(
                f"Enter a number from 1 to {len(choices)}, or a choice identifier.",
                f"请输入 1 到 {len(choices)} 的编号，或选项标识。",  # noqa: RUF001
            )

    def ask(self, en: str, zh: str, default: str = "", secret: bool = False, required: bool = False) -> str:
        """Ask for text, preserving an existing secret without displaying it."""
        while True:
            inquirer = self._inquirer()
            if inquirer is None:
                value = typer.prompt(
                    self.text(en, zh),
                    default=default,
                    type=str,
                    hide_input=secret,
                    show_default=not secret and bool(default),
                )
            elif secret:
                entered = str(inquirer.secret(message=self.text(en, zh)).execute())
                value = entered or default
            else:
                message = self.text(en, zh)
                if default:
                    message += f" [{default}]"
                entered = str(inquirer.text(message=message).execute()).strip()
                value = entered or default
            if required and not value.strip():
                self.say("This value cannot be empty.", "此项不能为空。")
                continue
            return value if secret else value.strip()

    def confirm(self, en: str, zh: str, default: bool = True) -> bool:
        """Accept English and Chinese confirmations in either UI language."""
        inquirer = self._inquirer()
        if inquirer is not None:
            return bool(inquirer.confirm(message=self.text(en, zh), default=default).execute())
        answer_default = self.text("y" if default else "n", "是" if default else "否")
        while True:
            answer = self.ask(en, zh, default=answer_default).casefold()
            if answer in {"y", "yes", "是"}:
                return True
            if answer in {"n", "no", "否"}:
                return False
            self.say("Enter y or n.", "请输入 是/否，或 y/n。")  # noqa: RUF001

    def integer(self, en: str, zh: str, default: int, minimum: int = 1, maximum: int | None = None) -> int:
        """Read an integer within an inclusive range, with localized retries."""
        if default < minimum or (maximum is not None and default > maximum):
            message = "Wizard integer default must be within the requested range."
            raise ValueError(message)
        while True:
            answer = self.ask(en, zh, default=str(default))
            try:
                value = int(answer)
            except ValueError:
                self.say("Enter an integer.", "请输入整数。")
                continue
            if value >= minimum and (maximum is None or value <= maximum):
                return value
            if maximum is None:
                self.say(f"Enter an integer of at least {minimum}.", f"请输入不小于 {minimum} 的整数。")
            else:
                self.say(
                    f"Enter an integer between {minimum} and {maximum}.",
                    f"请输入 {minimum} 到 {maximum} 之间的整数。",
                )


def _activity_phase(language: str, phase: str) -> str:
    labels = {
        "starting": ("starting", "准备中"),
        "resolving": ("checking packages", "检查依赖"),
        "resolving-mirror": ("checking mirror", "检查镜像"),
        "installing": ("downloading and installing", "下载并安装中"),
        "installing-mirror": ("downloading from mirror", "从镜像下载并安装"),
        "validating": ("validating", "验证安装"),
    }
    en, zh = labels.get(phase, ("working", "处理中"))
    return zh if language == "zh" else en
