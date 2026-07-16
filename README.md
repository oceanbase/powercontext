# PowerContext

PowerContext turns human-agent work into handoff-ready context.

The current Python package defines typed contracts for:

- resolving and storing external work evidence as Sources;
- maintaining versioned Artifacts with lifecycle and lineage;
- computing Trigger transitions from signals and state;
- composing those contracts without taking ownership of host-side infrastructure.

Storage, scheduling, model calls, queues, and framework integration remain explicit responsibilities of the host
application. The repository has not published a tagged release yet.

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
