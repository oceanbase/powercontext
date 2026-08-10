# PowerContext

PowerContext gives agents durable, project-scoped context. A later session can recover a decision, outcome, current
state, or next step without relying on chat history. PowerContext includes a local Server, SQLite storage, an
async Python client, a Core SDK, a CLI, and a Codex plugin.

PowerContext can be installed directly from its Git URL. Users need read access to that URL, but they do not need to
clone the repository or run commands from its working tree.

## Install for Codex

Prerequisites:

- macOS or Linux;
- [uv](https://docs.astral.sh/uv/getting-started/installation/);
- Codex CLI;
- read access to `oceanbase/powercontext`.

Install the tool and configure the Codex plugin:

```bash
uv tool install "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@master"
powercontext setup codex --source oceanbase/powercontext --ref master
```

You do not need to create or manage a repository checkout. Start the local service in a terminal:

```bash
powercontext server run
```

In another terminal, verify the package, plugin, Server, and database:

```bash
powercontext doctor
```

Start a new Codex session after installation. Open `/hooks` once and approve the PowerContext hook if Codex asks for
trust. The default database is persistent and requires no configuration.

See the [Codex quickstart](docs/en/docs/tutorials/codex-quickstart.md) for a first cross-session workflow.

## Choose an interface

| Interface | Use it for |
| --- | --- |
| Codex plugin | Restore relevant project memory and explicitly remember, revise, or retire entries while coding |
| CLI | Install the plugin, run or connect to the Server, inspect content, and diagnose an installation |
| Python client | Call the Server's Source and Memory API from an application |
| Core SDK | Embed PowerContext contracts or supply custom adapters in a Python system |
| HTTP and MCP | Integrate a non-Python process or an agent host with the running Server |

The [interface reference](docs/en/docs/reference/interfaces.md) explains the ownership boundary between these
surfaces. Installation, configuration, and troubleshooting live under [`docs/en/docs/`](docs/en/docs/index.md).

## Python projects

Add only the role the project imports:

```bash
uv add "powercontext[client] @ git+https://github.com/oceanbase/powercontext.git@master"
```

Available extras are `builtin`, `client`, `server`, and `cli`. The CLI always includes Server-backed content commands;
installing the `server` role also makes local Server process management available.

## Development

Repository contributors can install the locked environment and hooks with `make install`. Use `make test`,
`make check`, and `make docs-test` before opening a pull request. See [CONTRIBUTING.md](CONTRIBUTING.md) for the full
workflow and [`docs/en/development/`](docs/en/development/core-protocol.md) for implementation guides.

## License

PowerContext is licensed under the [Apache License 2.0](LICENSE).
