# Installer behavior contract

The JSON catalogs describe public installer inputs and observable outcomes. Both native shell entry points use
the same cases through `tests/test_installation_contract.py`. The catalogs are test inputs, not bootstrap downloads
or an additional installation manifest. RFC 1892 defines the overall installation and recovery boundary.

Run the offline contract with an existing Python and uv:

```bash
uv run pytest tests/test_installation_contract.py -q
```

Each file has `schema_version: 1` and a `cases` array. Case IDs must be unique across catalogs.

| Field | Meaning |
| --- | --- |
| `id` | Stable scenario name shown in test results |
| `args` | Arguments passed to the native installer |
| `git` | Whether Git is discoverable; defaults to false |
| `setup_exit` | Exit status of the fixture host operation; defaults to success |
| `reported_version` | Fixture CLI version; defaults to the wheel's `1.2.0` |
| `environment_file` | Create a protected existing configuration file for the case |
| `terminal` | Run a piped Bash installer through a POSIX controlling terminal |
| `config_exit`, `validate_exit`, `service_exit`, `doctor_exit` | Exit status of the fixture operation |
| `missing_commands` | Public command help paths omitted by the fixture |
| `expected.exit_code` | Installer completion or failure |
| `expected.runtime_installed` | Whether uv installed the tool launcher, including verification failures |
| `expected.server_extra` | Whether the selected profile contains the fixture Server dependency |
| `expected.selected_hosts` | Hosts actually submitted to setup; defaults to none |
| `expected.setup_ref` | Release tag actually submitted to setup |
| `expected.configuration_saved` | Whether interactive configuration wrote its selected file |
| `expected.service`, `expected.doctor` | Whether registration and same-file diagnostics produced their fixture results |
| `expected.setup_env_file` | Whether host setup received the selected configuration file |
| `expected.verification_failed` | Whether success must remain unannounced |
| `expected.error_contains` | Required diagnostic fragment |

The harness builds small standards-shaped wheels, installs them through real uv in private directories without
network access, and executes the resulting native launcher. Profile cases inspect an optional installed dependency.
Host cases observe the setup request and result through the fixture CLI. They do not start a real Agent, execute Git,
or establish Server readiness. No case requires a particular shell function name, private call order or subprocess count.

The installation CI matrix runs these cases on Linux, macOS and Windows. The same workflow also builds the actual
PowerContext wheel and runs `tests/native/test_installation.py` for bootstrap, configuration, HTTP readiness,
Memory persistence, profile replacement and version selection. `tests/native/test_install_sources.py` adds Linux
loopback index fault tests using the real downloader and uv, with only distribution endpoints redirected to fixtures.
Those source tests do not qualify public mirror availability or native Windows source handling. Native service CI
also runs `tests/native/test_service_installation.py` on disposable Linux/macOS runners with the built wheel, a real
user-service manager, configuration reconciliation, an offline stopped-service upgrade, and persistent Memory readback.
