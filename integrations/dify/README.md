# PowerContext Dify tools

An experimental Dify tool plugin with 19 HTTP-backed tools, aligned with the DSH tool catalog. It connects to an independently deployed PowerContext Server; the Server owns domain validation, authorization, storage and generation.

The installable source is [plugin/](plugin/). Runtime dependencies are isolated from the PowerContext Python package and pinned in `uv.lock` and `plugin/requirements.txt`. The plugin runner uses Python 3.12, Dify SDK 0.10.2, httpx 0.28.1 and jsonschema 4.25.1. The repository's supported Python versions are unchanged.

## Development and packaging

From the PowerContext repository root:

```sh
uv sync --locked --project integrations/dify --python 3.12
uv run --project integrations/dify python integrations/dify/generate_requirements.py --check
uv run --project integrations/dify python integrations/dify/generate_contract.py --check
uv run --project integrations/dify ruff check integrations/dify
uv run --project integrations/dify ruff format --check integrations/dify
uv run --project integrations/dify ty check --project integrations/dify --python integrations/dify/.venv
uv run --project integrations/dify python -m pytest integrations/dify/tests
uv run python -m pytest tests/e2e/test_dify_tools_http.py
dify plugin package integrations/dify/plugin -o .artifacts/dify/powercontext-0.0.1.difypkg
```

On Windows set `PYTHONUTF8=1`, or invoke Python with `-X utf8`, because the pinned SDK reads YAML with the process's default encoding. Install the root environment with `uv sync --locked` before the HTTP tests. `make dify-test` runs these source checks and HTTP tests; install the official Dify CLI separately for packaging. Packages, virtual environments and credentials must stay outside Git.

After changing runtime dependencies, update `uv.lock` and run `generate_requirements.py` without `--check` to regenerate the daemon's packaged pins. The check exports the locked production dependency set with uv and compares the complete file, including platform markers. Declaration generation and SDK validation share the hidden-parameter and Memory-kind policy in `plugin/powercontext_dify/policy.py`.

The generated contract is a closure of the 19 selected operations plus internal Scope resolution and protected Scope retrieval from `openapi/powercontext.yaml`. After an API change, regenerate with `generate_contract.py`, inspect the contract and declaration changes, and rerun the checks. This script does not modify Server models. It also records the JSON-text input parameters used by the adapter. Objects, arrays and nullable inputs are strings containing one JSON value; the plugin strictly decodes once and validates fields and complete receipts.

The supported declaration boundary is Dify 1.17.1 with its default `0.6.10-local` daemon. That daemon discards `input_schema`, so JSON-text inputs use valid `string` declarations and retain the full decoded schema in their model-facing descriptions. JSON null is the text `null`; nullable strings include JSON quotes; defaults such as empty reference arrays are the text `[]`. Ordinary scalars keep native declarations. Workflow output schemas explicitly use Draft 7: `$ref` points to the canonical validation schema, while sibling `type`/`properties`/`items` supply the metadata Dify reads directly. This preserves full response values, nullable fields and integer validation while allowing nested variable selection. See the [input and Workflow examples](plugin/README.md).

CI checks out Dify 1.17.1 at `8387590ace4a094de812b7847fc6a4c3a27cd52b` and daemon 0.6.10 at `1310a18b2f6bc6f18768a0a6265484830891433c`. SDK registrations first round-trip through the daemon's unmodified Go `ToolDeclaration`/`ToolParameter` entities with `encoding/json`; the tests then execute official Dify Pydantic models, parameter casting, model-schema builder and frontend output/selector functions. The HTTP scenarios apply those discovered declarations before registered SDK invocation.

For the same local probes, use Node 22.19+ and Go satisfying the daemon's `go.mod`, set `POWERCONTEXT_DIFY_SOURCE` and `POWERCONTEXT_DIFY_DAEMON_SOURCE` to the respective absolute pinned checkout paths, and run the tests above. `POWERCONTEXT_DIFY_GO` can specify a Go executable outside PATH. Without both source paths, compatibility scenarios are skipped; ordinary SDK/HTTP tests still run. A Dify sparse checkout needs the files listed in the `dify-tools` CI step; the daemon needs its full Go module. These probes verify source serialization and helper behavior, not a running Dify application, installed daemon or model.

## Usage and evidence

- [Installation, credentials and tools](plugin/README.md)
- [Scope mapping and trust boundary](scope-mapping.md)
- [Tool coverage](tool-coverage.md)
- [Acceptance evidence and deployment checklist](ACCEPTANCE.md)
- [Agent guidance](plugin/GUIDANCE.md)
- [Privacy](plugin/PRIVACY.md)

No Workflow/Chatflow DSL templates ship with this plugin. Their construction and clean reimport tests follow acceptance of the plugin submission in `langgenius/dify-plugins`, as tracked in [#1837](https://github.com/oceanbase/powercontext/issues/1837). Plugin installation and complex-object dispatch still require real Dify validation before publication.

## Coordination and attribution

[Tracking issue #1837](https://github.com/oceanbase/powercontext/issues/1837) tracks implementation, ownership, deployment acceptance, publication and later templates. [Draft PR #1558](https://github.com/oceanbase/powercontext/pull/1558) by @thunguo established earlier Dify tools, Scope/binding, privacy and acceptance work; this implementation follows the tools-focused scope proposed in #1837 and the current Server contract. The prior eight-tool/legacy strategy commit is not imported wholesale.

The local package identity is `knqiufan/powercontext`, version 0.0.1. A Marketplace publisher, maintenance owner and coexistence/upgrade policy for the existing `oceanbase/powermem` entry must be agreed before submission. Source implementation, plugin-repository acceptance and Marketplace availability are separate milestones.
