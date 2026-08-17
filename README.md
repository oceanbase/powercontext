# PowerContext

PowerContext is PowerMem 2.0, the upgraded version of [PowerMem](https://www.powermem.ai/). It gives agents durable,
project-scoped context. A later session can recover a decision, outcome, current state, or next step without relying
on chat history. PowerContext includes a local Server, SQLite storage, an async Python client, a Core SDK, a CLI, and
a Codex plugin.

PowerContext can be installed directly from its Git URL. Users need read access to that URL, but they do not need to
clone the repository or run commands from its working tree.

## Get started with Codex

The [Codex quickstart](docs/en/docs/tutorials/codex-quickstart.md) covers prerequisites, installation, Server startup,
plugin trust, verification, and a cross-session Memory workflow. It does not require a repository checkout. For a
specific task after installation, use the [documentation overview](docs/en/docs/index.md).

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

## Benchmarks

### [LOCOMO](https://github.com/snap-research/locomo)

| Metric | PowerContext | [PowerMem](https://www.powermem.ai/benchmark) | Full-context baseline |
| --- | ---: | ---: | ---: |
| Accuracy | **90.78%** (1,398/1,540) | 87.79% | 52.9% |
| Search p95 latency | **1.38 s** | 1.44 s | 17.12 s |
| Answer tokens / question | **~1.65 k** | ~0.9 k | 26 k |

The PowerContext results come from a full 1,540-question run across all 10 LOCOMO conversations.

## Development

Repository contributors can install the locked environment and hooks with `make install`. Use `make test`,
`make check`, and `make docs-test` before opening a pull request. See [CONTRIBUTING.md](CONTRIBUTING.md) for the full
workflow and [`docs/en/development/`](docs/en/development/core-protocol.md) for implementation guides.

## License

PowerContext is licensed under the [Apache License 2.0](LICENSE).
