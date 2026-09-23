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

"""Language-independent integration lifecycle with native Python callbacks."""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING

from .targets import Target, load_targets

if TYPE_CHECKING:
    from powercontext.cli.system import Diagnostic


@dataclass(frozen=True, slots=True)
class HostAdapter:
    """Native callbacks injected into shared setup, resource generation, and doctor."""

    target: Target

    @property
    def name(self) -> str:
        return self.target.target

    @property
    def label(self) -> str:
        return self.target.label

    @property
    def setup_options(self) -> tuple[str, ...]:
        return self.target.setup.options

    def callback(self, prefix: str, suffix: str):
        spec = self.target.setup
        if spec.module is None:
            module = import_module(f"{__package__}.packages")
            return partial(getattr(module, f"{prefix}_package_{suffix}"), self.target)
        module = import_module(f"{__package__}.{spec.module}")
        return getattr(module, f"{prefix}_{self.name.replace('-', '_')}_{suffix}")

    def prepare(self, directory: Path, *, server_url: str | None = None) -> None:
        from .resources import install_resources

        install_resources(self.name, directory, server_url=server_url)

    def diagnose(self, *, server: bool = False, **options) -> dict[str, Diagnostic]:
        from .hook_runtime import hook_client_diagnostic
        from .transport import add_transport_diagnostic

        diagnostics = {**self.callback("run", "diagnostics")(**options), "client": hook_client_diagnostic()}
        add_transport_diagnostic(diagnostics, self.name)
        if server and ("transport" not in diagnostics or diagnostics["transport"].status.value == "degraded"):
            from powercontext.cli.system import Diagnostic, DiagnosticStatus, server_diagnostics

            from .native_transport import resolve_host_connection

            try:
                diagnostics.update(server_diagnostics(resolve_host_connection(self.name)))
            except ValueError:
                diagnostics["configuration"] = Diagnostic(
                    DiagnosticStatus.FAILED, "Invalid native connection settings."
                )

        return diagnostics

    def install(
        self,
        *,
        source: str,
        ref: str,
        server_url: str | None = None,
        capture_prompts: bool = True,
        allow_insecure_http: bool | None = None,
        json_output: bool = False,
        python: str | None = None,
        destination: Path | None = None,
    ):
        from powercontext.cli.system import SetupError

        from .hook_runtime import require_hook_client
        from .transport import prepare_setup_transport, save_setup_transport

        for key, value in {"python": python, "destination": destination}.items():
            if value is not None and key not in self.setup_options:
                raise SetupError(f"{self.name} does not accept --{key}.")
        require_hook_client()
        transport = prepare_setup_transport(
            self.name,
            server_url=server_url,
            allow_insecure_http=allow_insecure_http,
            json_output=json_output,
            destination=destination,
        )
        options = {
            "server_url": transport.server_url,
            "capture_prompts": capture_prompts,
            "allow_insecure_http": transport.allow_insecure_http,
            "python": python,
            "destination": destination,
        }
        resource_dir = Path(source) / self.target.source / self.target.resource_dir
        if resource_dir.is_dir():
            self.prepare(resource_dir)
        result = self.callback("install", "plugin")(
            source=source, ref=ref, **{key: options[key] for key in self.setup_options}
        )
        installation = {key: str(value) for key in ("python", "destination") if (value := getattr(result, key, None))}
        diagnostic_options = {key: value for key, value in installation.items() if key in self.setup_options}
        if not self.target.setup.verifies:
            failures = [
                f"{key}: {check.detail}"
                for key, check in self.callback("run", "diagnostics")(**diagnostic_options).items()
                if not check.ok
            ]
            if failures:
                raise SetupError(f"post-install verification failed: {'; '.join(failures)}")
        installation["source"] = str(Path(source).resolve()) if Path(source).is_dir() else source
        installation["ref"] = ref
        save_setup_transport(transport, installation=installation)
        return result


HOST_ADAPTERS = tuple(HostAdapter(target) for target in load_targets())


def host_adapter(name: str) -> HostAdapter:
    return next(adapter for adapter in HOST_ADAPTERS if adapter.name == name)
