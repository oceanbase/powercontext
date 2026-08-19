# PowerContext

**PowerContext is PowerMem 2.0, the upgraded version of [PowerMem](https://www.powermem.ai/).**

[![PyPI version](https://img.shields.io/pypi/v/powercontext)](https://pypi.org/project/powercontext/)
[![License Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Discord](https://img.shields.io/badge/Discord-community-5865F2?logo=discord&logoColor=white)](https://discord.com/invite/74cF8vbNEs)

PowerContext gives agents durable, project-scoped context. A later session can recover a decision, outcome, current state, or next step without relying on chat history. PowerContext includes a local Server, SQLite storage, an async Python client, a Core SDK, a CLI, a Codex plugin, and a DeepSeek Harness plugin.

## Get started

Use [Install and run](docs/en/docs/how-to/install-and-run.md) for prerequisites, installation, Server startup, and
verification. Then choose a host integration:

- [Codex quickstart](docs/en/docs/tutorials/codex-quickstart.md) provides a first cross-session Memory workflow.
- [Configure Codex](docs/en/docs/how-to/configure-codex.md) and
  [Configure DeepSeek Harness](docs/en/docs/how-to/configure-dsh.md) cover host-specific setup.

For other tasks, start from the [documentation overview](docs/en/docs/index.md).

## Choose an interface

| Interface | Use it for |
| --- | --- |
| Codex plugin | Restore relevant project memory and explicitly remember, revise, or retire entries while coding |
| DeepSeek Harness plugin | Restore relevant project memory and explicitly remember, revise, or retire entries in DeepSeek Harness |
| CLI | Install the plugin, run or connect to the Server, inspect content, and diagnose an installation |
| Python client | Call the Server's Source and Memory API from an application |
| Core SDK | Embed PowerContext contracts or supply custom adapters in a Python system |
| HTTP and MCP | Integrate a non-Python process or an agent host with the running Server |

The [interface reference](docs/en/docs/reference/interfaces.md) explains the ownership boundary between these
surfaces. Installation, configuration, and troubleshooting live under [`docs/en/docs/`](docs/en/docs/index.md).

## Python projects

Add only the role the project imports:

```bash
uv add "powercontext[client]==0.0.1"
```

Available extras are `builtin`, `client`, `server`, `cli`, and `tracing-otlp`. The CLI always includes Server-backed content commands; installing the `server` role also makes local Server process management available.

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
