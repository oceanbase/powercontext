# PowerContext

PowerContext turns human-agent work into handoff-ready context.

The current Python package defines typed contracts for:

- resolving and storing external work evidence as Sources;
- maintaining versioned Artifacts with lifecycle and lineage;
- computing Trigger transitions from signals and state;
- composing those contracts without taking ownership of host-side infrastructure.

The Core package leaves infrastructure ownership to the host. Optional extras provide the supported implementation and
process roles:

| Extra | Includes |
| --- | --- |
| `builtin` | Builtin runtime, SQLite, OceanBase, scheduling, and inference |
| `client` | Core contracts and the async Python SDK |
| `server` | Builtin runtime, HTTP, and MCP |
| `cli` | Command shell, Builtin by default, and installed-role discovery |

Install a role directly when using its Python API:

```bash
uv add "powercontext[builtin]"
uv add "powercontext[client]"
uv add "powercontext[server]"
```

The CLI defaults to the `builtin` command. Add Client or Server to the same environment only when their commands are
needed:

```bash
uv add "powercontext[cli]"
uv add "powercontext[cli,client]"
uv add "powercontext[cli,server]"
```

Commands are discovered from installed entry points. A role that is not installed does not appear in CLI help.

The repository has not published a tagged release yet.

## Documentation

- [Project overview](docs/en/index.md)
- [Core Protocol integration guide](docs/en/development/core-protocol.md)
- [API reference](docs/en/modules.md)
- [RFCs](docs/en/rfcs/README.md)

## Development

Install the environment and repository hooks:

```bash
make install
```

Run the test and quality suites:

```bash
make test
make check
```

Build the documentation strictly or serve it locally:

```bash
make docs-test
make docs
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the contribution workflow.

## License

PowerContext is licensed under the [Apache License 2.0](LICENSE).
