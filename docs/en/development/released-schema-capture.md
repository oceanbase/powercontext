# Released Server schema capture

`scripts/capture_release_schema.py` collects evidence for registering a supported
source release. It creates a fresh environment from the official PowerContext
1.4.0 wheel, verifies the wheel against PyPI's SHA256, starts that installed
Server, writes disposable Memory and optional Topic/Code fixtures, then stops the
Server before reflecting its database with the installed release's drivers.
It does not invoke the current migration runtime or modify registered resources.

```sh
uv run python scripts/capture_release_schema.py sqlite \
  --configuration full-server-no-vector --output /tmp/release-140-capture
```

The output directory must not exist. Use only disposable databases. OceanBase
uses the local isolated tenant created by `.github/actions/setup-upgrade-oceanbase`
and requires `PC_UPGRADE_OCEANBASE_URL`; the shared fixture restricts its database
names and capture rejects a nonempty database before starting the Server.

The independent **Capture released Server schema** workflow runs all three real
backends and four configurations. Vector profiles use controlled embeddings with
dimensions 3 and 4; Code profiles also index a temporary repository and record
the separate Code cache hashes. Native backends require Linux CI. The Linux
process owner cleans up descendants, including detached embedded engines.

Artifacts contain raw schema definitions, data digests, engine and driver
versions, installed package versions, wheel URL/SHA256, configuration metadata,
Server readiness, and logs. `canonical-schema.json` contains the complete logical
inventory and its digest. Native definitions are parsed offline by the current
checkout's reviewed parser, whose source SHA256 is recorded; the source release's
older parser is not used. SQLite retains exact `sqlite_schema` rows excluding
SQLite-owned objects. No current runtime connects to the source database.
`status=captured` is written only after every phase
passes. Missing or incomplete artifacts are not valid baseline evidence. A
capture proves how the released version creates a database under that profile;
it does not prove target-version admission, migration correctness, or support for
every possible historical configuration. Review and register the collected
evidence separately, then run the complete upgrade acceptance matrix.

For an offline reproduction, `--wheel` and `--pypi-metadata` accept a cached wheel
and saved official PyPI release JSON. Their digest must agree; the manifest marks
metadata supplied from disk so it cannot be mistaken for a live PyPI lookup.
