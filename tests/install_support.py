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


"""Native-operation substitutes for CLI routing tests; the shared driver still runs."""

from types import SimpleNamespace

from powercontext.cli.installation import installers


def stub_installer(monkeypatch, host, callback):
    from powercontext.cli import transport

    installer_type = type(installers()[host]())
    prepare = transport.prepare_setup_transport
    connection = None

    def prepare_connection(*args, **kwargs):
        nonlocal connection
        connection = prepare(*args, **kwargs)
        return connection

    def install(self, source, **install_options):
        assert connection is not None
        assert install_options == ({"connection": connection} if host == "minimax" else {})
        options = vars(self) | {
            "host": host,
            "source": source.location,
            "ref": source.ref,
            "server_url": connection.server_url,
            "allow_insecure_http": connection.allow_insecure_http,
        }
        return callback(SimpleNamespace(**options))

    def configure(self, connection):
        from powercontext.cli.installation import publish

        publish(writes=transport.setup_transport_updates(connection))
        return {}

    monkeypatch.setattr(transport, "prepare_setup_transport", prepare_connection)
    monkeypatch.setattr(installer_type, "install", install)
    monkeypatch.setattr(installer_type, "configure", configure)
    if hasattr(installer_type, "enable"):
        monkeypatch.setattr(installer_type, "enable", lambda self: None)


def configure_opencode(monkeypatch, config):
    from powercontext.cli import opencode

    monkeypatch.setenv("OPENCODE_CONFIG_DIR", str(config))
    monkeypatch.setenv("POWERCONTEXT_HOME", str(config.parent / "data"))
    monkeypatch.setattr(opencode, "opencode_executable", lambda: "opencode")
    monkeypatch.setattr(opencode, "_version", lambda: "1.18.21")
    monkeypatch.setattr(opencode, "opencode_config_dir", lambda: config)
