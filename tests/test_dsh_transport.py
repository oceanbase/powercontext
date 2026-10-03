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

"""Native DSH composition and setup policy, without mutating the user's host."""

import json
import os
import shutil
import subprocess
from pathlib import Path
from shutil import which
from unittest.mock import Mock

import pytest
from typer.testing import CliRunner

from powercontext.cli.app import create_cli
from powercontext.cli.dsh_transport import matching_dsh_consent, read_dsh_settings, validate_dsh_setup_transport
from powercontext.cli.native_transport import resolve_host_transport
from powercontext.cli.system import setup_app
from powercontext.cli.transport import prepare_setup_transport


@pytest.fixture
def dsh_profile(tmp_path, monkeypatch, request):
    """Use a real installed DSH parser, with an entirely disposable profile."""
    import powercontext.cli.dsh as dsh

    executable = os.environ.get("DSH_TEST_EXECUTABLE") or which("dsh.cmd" if os.name == "nt" else "dsh")
    if not executable:
        if request.config.getoption("--require-dsh-runtime"):
            pytest.fail("Required DSH runtime is unavailable; set DSH_TEST_EXECUTABLE")
        pytest.skip("Native DSH composition requires DSH; set DSH_TEST_EXECUTABLE to select a runtime")
    monkeypatch.setattr(dsh, "dsh_executable", lambda: executable)
    for key in list(os.environ):
        if key.startswith("POWERCONTEXT_") or key == "DSH_PROFILE":
            monkeypatch.delenv(key)
    monkeypatch.setenv("POWERCONTEXT_CLIENT_CONFIG_FILE", str(tmp_path / "clients.json"))
    monkeypatch.setenv("DSH_HOME", str(tmp_path / "dsh"))
    monkeypatch.chdir(tmp_path)
    profile = tmp_path / "dsh/profiles/web"
    profile.mkdir(parents=True)
    (profile / "package.json").write_text(json.dumps({"dsh": {"profile": {"bundles": []}}}))
    return profile


@pytest.fixture(scope="session")
def dsh_config_boot(request):
    """Resolve the pinned native configuration API package independently of the legacy SDK."""
    location = os.environ.get("DSH_TEST_CONFIG_BOOT")
    if location is None and (node := which("node")):
        manifest = (
            Path(__file__).resolve().parents[1]
            / "integrations/dsh/plugins/powercontext/tests/config-runtime/package.json"
        )
        result = subprocess.run(
            [
                node,
                "--input-type=module",
                "-e",
                "import { createRequire } from 'node:module'; "
                "process.stdout.write(createRequire(process.argv[1]).resolve('@deepseek-ai/dsh-app-boot'))",
                str(manifest),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        if result.returncode == 0:
            location = result.stdout
    if not location or not Path(location).is_file():
        if request.config.getoption("--require-dsh-runtime") or request.config.getoption(
            "--require-dsh-config-runtime"
        ):
            pytest.fail("Required DSH configuration runtime is unavailable; install tests/config-runtime dependencies")
        pytest.skip("Native DSH compatibility tests require tests/config-runtime dependencies")
    return Path(location)


@pytest.fixture
def dsh_native_api_profile(dsh_config_boot, tmp_path, monkeypatch):
    """Use native 0.2 configuration APIs with a synthetic carrier and disposable profile."""
    import powercontext.cli.dsh as dsh

    carrier = tmp_path / "carrier"
    package = carrier / "node_modules/@deepseek-ai/dsh"
    package.mkdir(parents=True)
    (package / "package.json").write_text(
        json.dumps({
            "name": "@deepseek-ai/dsh",
            "dependencies": {
                "@deepseek-ai/dsh-app-boot": "0.2.0-rc.2",
                "@deepseek-ai/dsh-plugin-manager": "0.2.0-rc.2",
            },
        })
    )
    boot = package.parent / "dsh-app-boot"
    boot.mkdir()
    (boot / "package.json").write_text(json.dumps({"type": "module", "main": str(dsh_config_boot)}))
    executable = carrier / "dsh"
    executable.write_text("// Configuration inspection fixture; not an executable host.\n")
    monkeypatch.setattr(dsh, "dsh_executable", lambda: str(executable))
    for key in list(os.environ):
        if key.startswith("POWERCONTEXT_") or key == "DSH_PROFILE":
            monkeypatch.delenv(key)
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("POWERCONTEXT_CLIENT_CONFIG_FILE", str(tmp_path / "clients.json"))
    monkeypatch.setenv("DSH_HOME", str(tmp_path / "dsh"))
    profile = tmp_path / "dsh/profiles/web"
    profile.mkdir(parents=True)
    (profile / "package.json").write_text(json.dumps({"dsh": {"profile": {"bundles": []}}}))
    return profile


def write_patch(profile, content):
    path = profile / "cordis.patch.yml"
    path.write_text(content, encoding="utf-8")
    return path


def plugin_source(tmp_path):
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    (plugin / "package.json").write_text(
        json.dumps({"name": "powercontext-dsh", "dsh": {"bundle": {"patch": "cordis.patch.yml"}}})
    )
    (plugin / "cordis.patch.yml").write_text(
        "- insert:\n    - id: powercontext-dsh\n      name: powercontext-dsh\n      config: {}\n"
    )
    (plugin / "lib").mkdir()
    (plugin / "lib/index.js").write_text("export const name = 'powercontext-dsh'\n")
    return plugin


def test_existing_customizations_allow_public_setup_and_preserve_files(dsh_profile, tmp_path, monkeypatch):
    import powercontext.cli.dsh as dsh

    patch = write_patch(
        dsh_profile,
        "- insert:\n    - id: ui\n      name: ui\n- id: ui\n  config:\n    theme: dark\n    title: 自定义界面\n    model: !!js process.env.MODEL\n",
    )
    source = plugin_source(tmp_path)
    shutil.copytree(source, dsh_profile / "node_modules/powercontext-dsh")
    (dsh_profile / "package.json").write_text(
        json.dumps({
            "dependencies": {"powercontext-dsh": f"link:{source.as_posix()}"},
            "dsh": {"profile": {"bundles": ["powercontext-dsh"]}},
        })
    )
    original = {path: path.read_bytes() for path in dsh_profile.iterdir() if path.is_file()}
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "data"))
    installer = Mock(return_value="id: powercontext-dsh\n")
    monkeypatch.setattr(dsh, "_run_dsh", installer)
    for _ in range(2):
        result = CliRunner().invoke(create_cli([setup_app]), ["setup", "dsh", "--source", str(source), "--json"])
        assert result.exit_code == 0, result.output
        assert (
            json.loads((tmp_path / "clients.json").read_text())["hosts"]["dsh"]["server_url"] == "http://127.0.0.1:8000"
        )
    assert patch.read_bytes() == original[patch]
    assert {path: path.read_bytes() for path in dsh_profile.iterdir() if path.is_file()} == original


@pytest.mark.parametrize("relative", ["profiles/web/cordis.patch.yml", "cordis.patch.yml"])
def test_matching_native_transport_passes_and_conflicts_do_not_install(dsh_profile, tmp_path, monkeypatch, relative):
    import powercontext.cli.dsh as dsh

    patch = tmp_path / "dsh" / relative
    patch.write_text("- id: powercontext-dsh\n  config:\n    baseUrl: https://memory.example\n")
    assert prepare_setup_transport("dsh", server_url="https://memory.example").server_url == "https://memory.example"
    installer = Mock()
    monkeypatch.setattr(dsh, "_run_dsh", installer)
    result = CliRunner().invoke(
        create_cli([setup_app]), ["setup", "dsh", "--server-url", "https://other.example", "--json"]
    )
    assert result.exit_code == 1
    assert "baseUrl conflicts" in result.output
    installer.assert_not_called()
    assert not (tmp_path / "clients.json").exists()


def test_setup_uses_web_profile_and_home_layer_wins(dsh_profile, monkeypatch):
    write_patch(dsh_profile, "- id: powercontext-dsh\n  config:\n    baseUrl: https://profile.example\n")
    write_patch(dsh_profile.parent.parent, "- id: powercontext-dsh\n  config:\n    baseUrl: https://home.example\n")
    monkeypatch.setenv("DSH_PROFILE", "other")
    assert prepare_setup_transport("dsh").server_url == "https://home.example"


def test_doctor_reads_the_selected_runtime_profile(dsh_profile, monkeypatch):
    profile = dsh_profile.parent / "custom"
    profile.mkdir()
    (profile / "package.json").write_bytes((dsh_profile / "package.json").read_bytes())
    write_patch(
        profile,
        "- insert:\n    - id: powercontext-dsh\n      name: powercontext-dsh\n"
        "      config:\n        baseUrl: https://custom.example\n",
    )
    monkeypatch.setenv("DSH_PROFILE", "custom")
    assert resolve_host_transport("dsh") == ("https://custom.example", False)


@pytest.mark.parametrize(
    "patch",
    [
        "- id: powercontext-dsh\n  config:\n    baseUrl: !!js (() => { throw new Error('secret-value') })()\n",
        "- id: powercontext-dsh\n  config:\n    allowInsecureHttp: !!js process.env.SECRET\n",
        "- id: powercontext-dsh\n  disabled: true\n",
        "- insert:\n    - id: powercontext-dsh\n      name: powercontext-dsh\n",
        "- id: powercontext-dsh\n  name: wrong-plugin\n  config: {}\n",
        "- id: powercontext-dsh\n  config: [broken: secret-value\n",
    ],
)
def test_unverifiable_relevant_configuration_is_redacted(dsh_profile, patch):
    path = write_patch(dsh_profile, patch)
    with pytest.raises(ValueError) as error:
        read_dsh_settings(prospective=True)
    assert "secret-value" not in str(error.value)
    assert path.read_text() == patch


@pytest.mark.parametrize("profile_fixture", ["dsh_profile", "dsh_native_api_profile"])
@pytest.mark.parametrize("group", [{"group": True}, {"name": "@deepseek-ai/cordis-plugin-group"}])
@pytest.mark.parametrize("nested", [False, True])
def test_setup_rejects_duplicate_powercontext_inside_native_groups(
    request, profile_fixture, group, nested, tmp_path, monkeypatch
):
    import powercontext.cli.dsh as dsh

    profile = request.getfixturevalue(profile_fixture)
    row = {
        "id": "extra-group",
        **group,
        "config": [
            {
                "id": "powercontext-dsh",
                "name": "powercontext-dsh",
                "config": {"baseUrl": "https://unexpected.example"},
            }
        ],
    }
    if nested:
        row = {"id": "outer-group", "group": True, "config": [row]}
    patch = write_patch(profile, json.dumps([{"insert": [row]}]))
    clients = tmp_path / "clients.json"
    clients.write_text('{"version":1,"hosts":{"dsh":{"server_url":"https://saved.example"}}}')
    original = {path: path.read_bytes() for path in (patch, clients)}
    installer = Mock()
    monkeypatch.setattr(dsh, "_run_dsh", installer)

    with pytest.raises(ValueError, match="Multiple PowerContext entries"):
        read_dsh_settings(prospective=True)
    result = CliRunner().invoke(
        create_cli([setup_app]), ["setup", "dsh", "--server-url", "https://selected.example", "--json"]
    )
    assert result.exit_code == 1
    assert "Multiple PowerContext entries" in result.output
    installer.assert_not_called()
    assert {path: path.read_bytes() for path in original} == original


@pytest.mark.parametrize("profile_fixture", ["dsh_profile", "dsh_native_api_profile"])
def test_a_single_powercontext_inside_a_named_group_is_inspected(request, profile_fixture, tmp_path):
    request.getfixturevalue(profile_fixture)
    source = plugin_source(tmp_path)
    (source / "cordis.patch.yml").write_text(
        json.dumps([
            {
                "insert": [
                    {
                        "id": "powercontext-group",
                        "name": "@deepseek-ai/cordis-plugin-group",
                        "config": [
                            {
                                "id": "powercontext-dsh",
                                "name": "powercontext-dsh",
                                "config": {"baseUrl": "https://selected.example", "allowInsecureHttp": False},
                            }
                        ],
                    }
                ]
            }
        ])
    )
    assert read_dsh_settings(candidate=source) == {
        "baseUrl": "https://selected.example",
        "allowInsecureHttp": False,
    }


@pytest.mark.parametrize("profile_fixture", ["dsh_profile", "dsh_native_api_profile"])
@pytest.mark.parametrize("nested", [False, True])
def test_setup_rejects_powercontext_in_native_include_trees(request, profile_fixture, nested, tmp_path, monkeypatch):
    from powercontext.cli import dsh
    from powercontext.cli.authorization import credential_path, write_stored_authorization

    profile = request.getfixturevalue(profile_fixture)
    directory = profile / "custom files"
    directory.mkdir()
    included = directory / "entries.json"
    powercontext = {
        "id": "powercontext-dsh",
        "name": "powercontext-dsh",
        "config": {
            "baseUrl": "https://unexpected.example",
        },
    }
    included.write_text(json.dumps([powercontext] if not nested else []))
    row = {
        "id": "custom-include",
        "name": "@deepseek-ai/cordis-plugin-include",
        "config": {
            "path": "./custom files/entries.json",
        },
    }
    if nested:
        # Resolve the second include from its containing file, and apply its
        # own insert patch before looking through the named group it creates.
        outer = directory / "outer.yml"
        outer.write_text(
            json.dumps([
                {
                    "id": "inner-include",
                    "name": "cordis:include",
                    "config": {
                        "path": "./entries.json",
                        "patches": [
                            {
                                "insert": [
                                    {
                                        "id": "included-group",
                                        "name": "@deepseek-ai/cordis-plugin-group",
                                        "config": [powercontext],
                                    }
                                ]
                            }
                        ],
                    },
                }
            ])
        )
        row["config"]["path"] = "./custom files/outer.yml"
        row = {"id": "outer-group", "group": True, "config": [row]}
    patch = write_patch(profile, json.dumps([{"insert": [row]}]))
    clients = tmp_path / "clients.json"
    clients.write_text('{"version":1,"hosts":{"dsh":{"server_url":"https://saved.example"}}}')
    credentials = credential_path("dsh")
    write_stored_authorization(credentials, server_url="https://saved.example", value="saved-token")
    original = {path: path.read_bytes() for path in (patch, included, clients, credentials)}
    installer = Mock()
    monkeypatch.setattr(dsh, "_run_dsh", installer)
    monkeypatch.setenv("POWERCONTEXT_DSH_AUTHORIZATION", "Bearer new-secret-token")

    with pytest.raises(ValueError, match="Multiple PowerContext entries"):
        read_dsh_settings(prospective=True)
    result = CliRunner().invoke(
        create_cli([setup_app]), ["setup", "dsh", "--server-url", "https://selected.example", "--json"]
    )
    assert result.exit_code == 1
    assert "Multiple PowerContext entries" in result.output
    assert "secret-token" not in result.output
    installer.assert_not_called()
    assert {path: path.read_bytes() for path in original} == original


@pytest.mark.parametrize("profile_fixture", ["dsh_profile", "dsh_native_api_profile"])
def test_setup_preserves_static_native_ui_includes_and_unevaluated_model_expressions(
    request, profile_fixture, tmp_path, monkeypatch
):
    from powercontext.cli import dsh

    profile = request.getfixturevalue(profile_fixture)
    source = plugin_source(tmp_path)
    shutil.copytree(source, profile / "node_modules/powercontext-dsh")
    (profile / "package.json").write_text(
        json.dumps({
            "dependencies": {"powercontext-dsh": f"link:{source.as_posix()}"},
            "dsh": {"profile": {"bundles": ["powercontext-dsh"]}},
        })
    )
    directory = profile / "custom files"
    directory.mkdir()
    (directory / "ui.yml").write_text(
        "- id: ui\n  name: ui\n  config:\n    title: 自定义界面\n"
        "    model: !!js (() => { throw new Error('secret-value') })()\n",
        encoding="utf-8",
    )
    (directory / "outer.json").write_text(
        json.dumps([
            {
                "id": "nested-include",
                "name": "@deepseek-ai/cordis-plugin-include",
                "config": {
                    "path": "./ui.yml",
                    "patches": [{"id": "ui", "disabled": False}],
                },
            }
        ])
    )
    # All outer layers resolve include paths from the profile root, even when
    # the include itself comes from the home-level patch file.
    write_patch(
        profile.parent.parent,
        json.dumps([
            {
                "insert": [
                    {
                        "id": "ui-group",
                        "name": "@deepseek-ai/cordis-plugin-group",
                        "config": [
                            {
                                "id": "ui-include",
                                "name": "cordis:include",
                                "config": {"path": "./custom files/outer.json"},
                            }
                        ],
                    }
                ]
            }
        ]),
    )
    original = {path: path.read_bytes() for path in profile.parent.parent.rglob("*") if path.is_file()}
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(dsh, "_run_dsh", Mock(return_value="id: powercontext-dsh\n"))
    for _ in range(2):
        result = CliRunner().invoke(
            create_cli([setup_app]),
            ["setup", "dsh", "--source", str(source), "--server-url", "https://selected.example", "--json"],
        )
        assert result.exit_code == 0, result.output
        assert read_dsh_settings(require_installed=True) == {}
        assert json.loads((tmp_path / "clients.json").read_text())["hosts"]["dsh"]["server_url"] == (
            "https://selected.example"
        )
    assert {path: path.read_bytes() for path in original} == original


@pytest.mark.parametrize("profile_fixture", ["dsh_profile", "dsh_native_api_profile"])
def test_installed_native_include_patches_define_powercontext_transport(request, profile_fixture, tmp_path):
    profile = request.getfixturevalue(profile_fixture)
    included = profile / "transport.yml"
    included.write_text(
        "- id: powercontext-dsh\n  name: powercontext-dsh\n  config:\n    baseUrl: https://unexpected.example\n"
    )
    source = plugin_source(tmp_path)
    (source / "cordis.patch.yml").write_text(
        json.dumps([
            {
                "insert": [
                    {
                        "id": "transport-include",
                        "name": "@deepseek-ai/cordis-plugin-include",
                        "config": {
                            "path": included.as_uri(),
                            "patches": [
                                {
                                    "id": "powercontext-dsh",
                                    "config": {
                                        "baseUrl": "http://selected.example",
                                        "allowInsecureHttp": True,
                                    },
                                }
                            ],
                        },
                    }
                ]
            }
        ])
    )
    shutil.copytree(source, profile / "node_modules/powercontext-dsh")
    (profile / "package.json").write_text(
        json.dumps({
            "dependencies": {"powercontext-dsh": f"link:{source.as_posix()}"},
            "dsh": {"profile": {"bundles": ["powercontext-dsh"]}},
        })
    )
    settings = {"baseUrl": "http://selected.example", "allowInsecureHttp": True}
    assert read_dsh_settings(candidate=source) == settings
    assert read_dsh_settings(require_installed=True) == settings
    assert prepare_setup_transport("dsh", server_url="http://selected.example", json_output=True).allow_insecure_http
    with pytest.raises(ValueError, match="HTTP consent"):
        validate_dsh_setup_transport(settings, "http://selected.example", False)
    bundle_patch = source / "cordis.patch.yml"
    patches = json.loads(bundle_patch.read_text())
    patches[0]["insert"].append({**patches[0]["insert"][0], "id": "second-include"})
    bundle_patch.write_text(json.dumps(patches))
    with pytest.raises(ValueError, match="Multiple PowerContext entries"):
        read_dsh_settings(candidate=source)


@pytest.mark.parametrize("profile_fixture", ["dsh_profile", "dsh_native_api_profile"])
@pytest.mark.parametrize("parent", [False, True])
@pytest.mark.parametrize("conditional", [False, True])
def test_native_include_checks_inherited_and_conditional_activation(request, profile_fixture, parent, conditional):
    profile = request.getfixturevalue(profile_fixture)
    (profile / "included.json").write_text(
        json.dumps([
            {
                "id": "powercontext-dsh",
                "name": "powercontext-dsh",
                "config": {},
            }
        ])
    )
    disabled = {"__jsExpr": "false"} if conditional else True
    row = {
        "id": "included",
        "name": "@deepseek-ai/cordis-plugin-include",
        "config": {
            "path": "./included.json",
        },
    }
    if parent:
        row = {"id": "named-group", "name": "@deepseek-ai/cordis-plugin-group", "config": [row]}
    row = {**row, "disabled": disabled}
    write_patch(profile, json.dumps([{"insert": [row]}]))
    if conditional:
        with pytest.raises(ValueError, match="conditionally enabled"):
            read_dsh_settings(prospective=True)
    else:
        assert read_dsh_settings(prospective=True) == {}


@pytest.mark.parametrize("profile_fixture", ["dsh_profile", "dsh_native_api_profile"])
@pytest.mark.parametrize(
    "case",
    [
        "dynamic-path",
        "dynamic-patches",
        "dynamic-target",
        "remote",
        "initial",
        "cycle",
        "dynamic-tree",
    ],
)
def test_unverifiable_native_include_trees_fail_without_writes_or_expression_evaluation(
    request, profile_fixture, case, tmp_path, monkeypatch
):
    from powercontext.cli import dsh

    profile = request.getfixturevalue(profile_fixture)
    path = profile / "included.yml"
    path.write_text("[]\n")
    config = {"path": "./included.yml"}
    if case == "dynamic-path":
        config["path"] = {"__jsExpr": "(() => { throw new Error('secret-value') })()"}
    elif case == "dynamic-patches":
        config["patches"] = {"__jsExpr": "process.env.SECRET"}
    elif case == "dynamic-target":
        config["patches"] = [{"id": {"__jsExpr": "process.env.SECRET"}, "config": {}}]
    elif case == "remote":
        config["path"] = "https://example.test/secret-value.yml"
    elif case == "initial":
        path.unlink()
        config["initial"] = []
    elif case == "cycle":
        (profile / "nested").mkdir()
        path.write_text(
            json.dumps([
                {
                    "id": "cycle",
                    "name": "cordis:include",
                    "config": {
                        "path": "./nested/../included.yml",
                    },
                }
            ])
        )
    elif case == "dynamic-tree":
        path.write_text("- id: dynamic-group\n  group: true\n  config: !!js process.env.SECRET\n")
    patch = write_patch(
        profile,
        json.dumps([
            {
                "insert": [
                    {
                        "id": "included",
                        "name": "@deepseek-ai/cordis-plugin-include",
                        "config": config,
                    }
                ]
            }
        ]),
    )
    original = {file: file.read_bytes() for file in profile.rglob("*") if file.is_file()}
    installer = Mock()
    monkeypatch.setattr(dsh, "_run_dsh", installer)
    result = CliRunner().invoke(
        create_cli([setup_app]), ["setup", "dsh", "--server-url", "https://selected.example", "--json"]
    )
    assert result.exit_code == 1
    assert "secret-value" not in result.output
    installer.assert_not_called()
    assert not (tmp_path / "clients.json").exists()
    assert not (profile.parent.parent / "powercontext/credentials.json").exists()
    assert patch.exists()
    assert {file: file.read_bytes() for file in original} == original
    assert path.exists() is (case != "initial")


@pytest.mark.parametrize("profile_fixture", ["dsh_profile", "dsh_native_api_profile"])
def test_postinstall_include_drift_preserves_saved_connection_and_credentials(
    request, profile_fixture, tmp_path, monkeypatch
):
    from powercontext.cli import dsh
    from powercontext.cli.authorization import credential_path, write_stored_authorization

    profile = request.getfixturevalue(profile_fixture)
    source = plugin_source(tmp_path)
    clients = tmp_path / "clients.json"
    clients.write_text('{"version":1,"hosts":{"dsh":{"server_url":"https://saved.example"}}}')
    credentials = credential_path("dsh")
    write_stored_authorization(credentials, server_url="https://saved.example", value="saved-token")
    original = {path: path.read_bytes() for path in (clients, credentials)}
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("POWERCONTEXT_DSH_AUTHORIZATION", "Bearer new-secret-token")

    def install(*_args):
        shutil.copytree(source, profile / "node_modules/powercontext-dsh")
        (profile / "package.json").write_text(json.dumps({"dsh": {"profile": {"bundles": ["powercontext-dsh"]}}}))
        (profile / "included.json").write_text(
            json.dumps([
                {
                    "id": "powercontext-dsh",
                    "name": "powercontext-dsh",
                    "config": {"baseUrl": "https://unexpected.example"},
                }
            ])
        )
        write_patch(
            profile,
            json.dumps([
                {
                    "insert": [
                        {
                            "id": "drift-include",
                            "name": "cordis:include",
                            "config": {"path": "./included.json"},
                        }
                    ]
                }
            ]),
        )
        return ""

    # Actual native parsing follows an injected change at the installation boundary.
    monkeypatch.setattr(dsh, "_run_dsh", install)
    result = CliRunner().invoke(
        create_cli([setup_app]),
        ["setup", "dsh", "--source", str(source), "--server-url", "https://selected.example", "--json"],
    )
    assert result.exit_code == 1
    assert "resulting configuration" in result.output
    assert "Multiple PowerContext entries" in result.output
    assert "secret-token" not in result.output
    assert {path: path.read_bytes() for path in original} == original


def test_candidate_bundle_is_checked_before_install(dsh_profile, tmp_path, monkeypatch):
    import powercontext.cli.dsh as dsh

    source = plugin_source(tmp_path)
    (source / "cordis.patch.yml").write_text(
        "- insert:\n    - id: powercontext-dsh\n      name: powercontext-dsh\n      config:\n        baseUrl: https://candidate.example\n"
    )
    installer = Mock()
    monkeypatch.setattr(dsh, "_run_dsh", installer)
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "data"))
    result = CliRunner().invoke(
        create_cli([setup_app]),
        ["setup", "dsh", "--source", str(source), "--server-url", "https://selected.example", "--json"],
    )
    assert result.exit_code == 1
    assert "baseUrl conflicts" in result.output
    installer.assert_not_called()
    assert not (tmp_path / "clients.json").exists()


def test_candidate_replaces_installed_bundle_without_duplicate_entries(dsh_profile, tmp_path):
    source = plugin_source(tmp_path)
    installed = dsh_profile / "node_modules/powercontext-dsh"
    installed.mkdir(parents=True)
    (installed / "package.json").write_bytes((source / "package.json").read_bytes())
    (installed / "cordis.patch.yml").write_bytes((source / "cordis.patch.yml").read_bytes())
    (dsh_profile / "package.json").write_text(json.dumps({"dsh": {"profile": {"bundles": ["powercontext-dsh"]}}}))
    write_patch(
        dsh_profile,
        "- id: powercontext-dsh\n  config:\n    baseUrl: http://memory.example\n    allowInsecureHttp: true\n",
    )
    assert read_dsh_settings(candidate=source)["baseUrl"] == "http://memory.example"
    assert resolve_host_transport("dsh") == ("http://memory.example", True)


def test_unreadable_bundle_is_not_assumed_to_be_unrelated(dsh_profile):
    (dsh_profile / "package.json").write_text(json.dumps({"dsh": {"profile": {"bundles": ["missing-bundle"]}}}))
    with pytest.raises(ValueError, match="Cannot read"):
        read_dsh_settings(prospective=True)


def test_remote_http_consent_and_environment_precedence(dsh_profile, monkeypatch):
    write_patch(
        dsh_profile,
        "- id: powercontext-dsh\n  config:\n    baseUrl: http://memory.example\n    allowInsecureHttp: true\n",
    )
    assert prepare_setup_transport("dsh", json_output=True).allow_insecure_http is True
    monkeypatch.setenv("POWERCONTEXT_DSH_BASE_URL", "http://other.example")
    with pytest.raises(RuntimeError, match="Remote HTTP"):
        prepare_setup_transport("dsh", server_url="http://other.example", json_output=True)
    monkeypatch.setenv("POWERCONTEXT_DSH_ALLOW_INSECURE_HTTP", "true")
    assert (
        prepare_setup_transport("dsh", server_url="http://other.example", json_output=True).allow_insecure_http is True
    )


@pytest.mark.parametrize(
    ("settings", "endpoint", "expected"),
    [
        ({"baseUrl": "http://a.example", "allowInsecureHttp": True}, "http://a.example/", True),
        ({"baseUrl": "http://a.example", "allowInsecureHttp": True}, "http://b.example", None),
        ({"allowInsecureHttp": False}, "http://b.example", False),
        ({}, "http://b.example", None),
    ],
)
def test_native_consent_is_endpoint_bound(settings, endpoint, expected):
    assert matching_dsh_consent(settings, endpoint) is expected


def test_explicit_refusal_cannot_be_overridden_by_native_permission(monkeypatch):
    for key in list(os.environ):
        if key.startswith("POWERCONTEXT_"):
            monkeypatch.delenv(key)
    with pytest.raises(ValueError, match="HTTP consent"):
        validate_dsh_setup_transport(
            {"baseUrl": "http://a.example", "allowInsecureHttp": True}, "http://a.example", False
        )


@pytest.mark.parametrize(
    ("patch", "reason"),
    [
        ("- id: powercontext-dsh\n  config:\n    baseUrl: https://unexpected.example\n", "baseUrl conflicts"),
        (
            json.dumps([
                {
                    "insert": [
                        {
                            "id": "extra-group",
                            "name": "@deepseek-ai/cordis-plugin-group",
                            "config": [{"id": "powercontext-dsh", "name": "powercontext-dsh", "config": {}}],
                        }
                    ]
                }
            ]),
            "Multiple PowerContext entries",
        ),
    ],
)
def test_setup_checks_actual_installed_transport_before_saving_connection(
    dsh_profile, tmp_path, monkeypatch, patch, reason
):
    import powercontext.cli.dsh as dsh

    source = plugin_source(tmp_path)
    clients = tmp_path / "clients.json"
    clients.write_text('{"version":1,"hosts":{"dsh":{"server_url":"https://saved.example"}}}')
    original = clients.read_bytes()
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "data"))

    def install(*_args):
        shutil.copytree(source, dsh_profile / "node_modules/powercontext-dsh")
        (dsh_profile / "package.json").write_text(json.dumps({"dsh": {"profile": {"bundles": ["powercontext-dsh"]}}}))
        write_patch(dsh_profile, patch)
        return ""

    # Inject configuration drift at the native installation boundary. The native
    # parser reads the resulting files; this does not simulate native add itself.
    monkeypatch.setattr(dsh, "_run_dsh", install)
    result = CliRunner().invoke(
        create_cli([setup_app]),
        ["setup", "dsh", "--source", str(source), "--server-url", "https://selected.example", "--json"],
    )
    assert result.exit_code == 1
    assert "resulting configuration" in result.output
    assert reason in result.output
    assert clients.read_bytes() == original


def test_current_transport_does_not_activate_an_inactive_dependency(dsh_profile, tmp_path):
    source = plugin_source(tmp_path)
    shutil.copytree(source, dsh_profile / "node_modules/powercontext-dsh")
    inactive = dsh_profile / "node_modules/transport-override"
    inactive.mkdir()
    (inactive / "package.json").write_text(
        json.dumps({"name": "transport-override", "dsh": {"bundle": {"patch": "cordis.patch.yml"}}})
    )
    (inactive / "cordis.patch.yml").write_text(
        "- id: powercontext-dsh\n  config:\n    baseUrl: https://inactive.example\n"
    )
    manifest = dsh_profile / "package.json"
    manifest.write_text(
        json.dumps({
            "dependencies": {"powercontext-dsh": f"link:{source.as_posix()}", "transport-override": "1.0.0"},
            "dsh": {"profile": {"bundles": ["powercontext-dsh"]}},
        })
    )
    original = manifest.read_bytes()
    assert read_dsh_settings(require_installed=True) == {}
    assert resolve_host_transport("dsh") == ("http://127.0.0.1:8000", False)
    assert manifest.read_bytes() == original


@pytest.mark.parametrize(
    ("settings", "arguments", "reason"),
    [
        ({"baseUrl": "https://native.example"}, ["--server-url", "https://selected.example"], "baseUrl conflicts"),
        ({"baseUrl": "http://remote.example"}, [], "Remote HTTP"),
        (
            {"baseUrl": "http://remote.example", "allowInsecureHttp": False},
            ["--allow-insecure-http"],
            "HTTP consent",
        ),
        (
            {"baseUrl": "https://selected.example", "allowInsecureHttp": True},
            ["--no-allow-insecure-http"],
            "HTTP consent",
        ),
    ],
)
def test_setup_transport_refusal_preserves_saved_state_without_a_native_runtime(
    tmp_path, monkeypatch, settings, arguments, reason
):
    from powercontext.cli import dsh, dsh_transport
    from powercontext.cli.authorization import credential_path, write_stored_authorization

    for key in list(os.environ):
        if key.startswith("POWERCONTEXT_"):
            monkeypatch.delenv(key)
    clients = tmp_path / "clients.json"
    clients.write_text('{"version":1,"hosts":{"dsh":{"server_url":"https://saved.example"}}}')
    monkeypatch.setenv("POWERCONTEXT_CLIENT_CONFIG_FILE", str(clients))
    monkeypatch.setattr(dsh_transport, "read_dsh_settings", lambda **_options: settings)
    installer = Mock()
    monkeypatch.setattr(dsh, "install_dsh_plugin", installer)
    original = clients.read_bytes()
    credentials = credential_path("dsh")
    write_stored_authorization(credentials, server_url="https://saved.example", value="saved-token")
    original_credentials = credentials.read_bytes()

    if "--server-url" not in arguments:
        arguments = ["--server-url", settings["baseUrl"], *arguments]
    result = CliRunner().invoke(create_cli([setup_app]), ["setup", "dsh", "--json", *arguments])

    assert result.exit_code == 1
    assert reason in result.output
    installer.assert_not_called()
    assert clients.read_bytes() == original
    assert credentials.read_bytes() == original_credentials


def test_matching_native_transport_is_accepted_without_a_native_runtime(monkeypatch):
    from powercontext.cli import dsh_transport

    for key in list(os.environ):
        if key.startswith("POWERCONTEXT_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("DSH_PROFILE", "custom")
    monkeypatch.setattr(
        dsh_transport,
        "read_dsh_settings",
        lambda **_options: {"baseUrl": "http://remote.example", "allowInsecureHttp": True},
    )
    transport = prepare_setup_transport("dsh", server_url="http://remote.example/", json_output=True)
    assert transport.server_url == "http://remote.example"
    assert transport.allow_insecure_http is True


def compatibility_profile(profile, tmp_path, *, incompatible="custom-bundle"):
    source = plugin_source(tmp_path)
    metadata = json.loads((source / "package.json").read_text())
    metadata["version"] = "1.0.0"
    if incompatible == "powercontext-dsh":
        metadata["peerDependencies"] = {"@deepseek-ai/dsh-app-boot": "999.0.0"}
    (source / "package.json").write_text(json.dumps(metadata))
    shutil.copytree(source, profile / "node_modules/powercontext-dsh")
    custom = profile / "node_modules/custom-bundle"
    custom.mkdir()
    metadata = {"name": "custom-bundle", "version": "1.0.0", "dsh": {"bundle": {"patch": "cordis.patch.yml"}}}
    if incompatible == "custom-bundle":
        metadata["peerDependencies"] = {"@deepseek-ai/dsh-app-boot": "999.0.0"}
    (custom / "package.json").write_text(json.dumps(metadata))
    (custom / "cordis.patch.yml").write_text(
        "- id: powercontext-dsh\n  config:\n    baseUrl: https://unexpected.example\n"
    )
    (profile / "package.json").write_text(
        json.dumps({
            "dependencies": {"powercontext-dsh": f"link:{source.as_posix()}", "custom-bundle": "1.0.0"},
            "dsh": {"profile": {"bundles": ["powercontext-dsh", "custom-bundle"]}},
        })
    )
    return source


def test_incompatible_third_party_bundle_is_excluded_from_first_and_repeated_setup(
    dsh_native_api_profile, tmp_path, monkeypatch
):
    from powercontext.cli import dsh

    profile = dsh_native_api_profile
    source = compatibility_profile(profile, tmp_path)
    original = {path: path.read_bytes() for path in profile.rglob("*") if path.is_file()}
    monkeypatch.setattr(dsh, "_run_dsh", Mock(return_value="id: powercontext-dsh\n"))
    for _ in range(2):
        result = CliRunner().invoke(
            create_cli([setup_app]),
            ["setup", "dsh", "--source", str(source), "--server-url", "https://selected.example", "--json"],
        )
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)["plugin"] == "powercontext-dsh"
        assert "incompatible third-party bundle" in result.stderr
        assert json.loads((tmp_path / "clients.json").read_text())["hosts"]["dsh"]["server_url"] == (
            "https://selected.example"
        )
    assert {path: path.read_bytes() for path in profile.rglob("*") if path.is_file()} == original


def test_incompatible_powercontext_still_fails_before_installation(dsh_native_api_profile, tmp_path, monkeypatch):
    from powercontext.cli import dsh

    source = compatibility_profile(dsh_native_api_profile, tmp_path, incompatible="powercontext-dsh")
    installer = Mock()
    monkeypatch.setattr(dsh, "_run_dsh", installer)
    result = CliRunner().invoke(
        create_cli([setup_app]),
        ["setup", "dsh", "--source", str(source), "--server-url", "https://selected.example", "--json"],
    )
    assert result.exit_code == 1
    assert "PowerContext is incompatible" in result.output
    installer.assert_not_called()
    assert not (tmp_path / "clients.json").exists()


def test_version_exemption_keeps_third_party_transport_overrides(dsh_native_api_profile, tmp_path, monkeypatch):
    from powercontext.cli import dsh

    source = compatibility_profile(dsh_native_api_profile, tmp_path)
    (dsh_native_api_profile / "compatibility.json").write_text(json.dumps({"custom-bundle@1.0.0": ["0.2.0-rc.2"]}))
    installer = Mock()
    monkeypatch.setattr(dsh, "_run_dsh", installer)
    result = CliRunner().invoke(
        create_cli([setup_app]),
        ["setup", "dsh", "--source", str(source), "--server-url", "https://selected.example", "--json"],
    )
    assert result.exit_code == 1
    assert "baseUrl conflicts" in result.output
    installer.assert_not_called()
    assert not (tmp_path / "clients.json").exists()


@pytest.mark.parametrize("available", ["module", "missing"])
def test_unavailable_host_configuration_apis_give_recovery_guidance(dsh_native_api_profile, tmp_path, available):
    package = tmp_path / "carrier/node_modules/@deepseek-ai/dsh-app-boot"
    (package / "package.json").write_text(json.dumps({"type": "module", "main": "index.js"}))
    entry = package / "index.js"
    if available == "module":
        entry.write_text("export {}\n")
    with pytest.raises(ValueError, match="upgrade or reinstall DSH"):
        read_dsh_settings()
