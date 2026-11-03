# Installer behavior contract

Both shell installers run against the JSON catalogs in this directory through `tests/test_installation_contract.py`. Add a case here when changing a public argument or observable installer behavior.

## Run the contract tests

With Python and uv available, run from the repository root:

```bash
uv run pytest tests/test_installation_contract.py -q
```

The harness builds small fixture wheels, installs them with real uv in temporary directories without network access, and runs the installed launcher. Profile cases check the installed dependency set. Host cases use a fixture CLI to record the setup arguments and simulate success or failure.

## Add a case

Each catalog has `schema_version: 1` and a `cases` array. Give every case an ID unique across all catalogs. Describe the public arguments and the resulting installation state or error. Avoid assertions about private function names or call order.

| Field | Meaning |
| --- | --- |
| `id` | Stable scenario name shown in test results |
| `args` | Arguments passed to the native installer |
| `git` | Whether Git is discoverable; defaults to false |
| `setup_style` | `group` models published per-host subcommands and missing setup in client-only installs; defaults to the unified callback |
| `unicode_paths` | Use a non-ASCII tool directory; Windows also starts with OEM output decoding and checks encoding restoration |
| `setup_exit` | Exit status of the fixture host operation; defaults to success |
| `reported_version` | Fixture CLI version; defaults to the wheel's `1.2.0` |
| `environment_file` | Create a protected existing configuration file for the case |
| `missing_commands` | Public command help paths omitted by the fixture |
| `expected.exit_code` | Expected installer process exit code |
| `expected.runtime_installed` | Whether uv installed the tool launcher, including verification failures |
| `expected.server_extra` | Whether the selected profile contains the fixture Server dependency |
| `expected.selected_hosts` | Hosts actually submitted to setup; defaults to none |
| `expected.setup_ref` | Release tag actually submitted to setup |
| `expected.setup_env_file` | Whether host setup received the selected configuration file |
| `expected.verification_failed` | Whether the Runtime installed message must be absent |
| `expected.error_contains` | Required diagnostic fragment |

## Choose the right test suite

| Suite | Coverage |
| --- | --- |
| `tests/test_installation_contract.py` | Shared installer arguments, profiles, command verification, host selection, and partial host failures using fixture wheels |
| `tests/native/test_install_sources.py` | Source selection, fallback, and version resolution with real uv and a controlled loopback index on Linux |
| `tests/native/test_installation.py` | Actual PowerContext wheel, prerequisite bootstrap, HTTP readiness, Memory persistence, offline reinstall, profiles, and versions |

The installation CI workflow builds the PowerContext wheel and runs the shared contract and native acceptance on Linux, macOS, and Windows. Set `POWERCONTEXT_INSTALL_WHEEL` to a built wheel to run actual-wheel acceptance locally; see `.github/workflows/installation.yml` for the build and invocation.

Fixture hosts do not exercise a real Agent or service manager. Controlled sources do not check public mirror availability. Personal-service acceptance runs separately in `.github/workflows/native-service.yml`.
