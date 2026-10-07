# Integration guidance authoring

Packaged `powercontext-project-context` resources are generated from this directory.
Change `base/` for common instructions and `hosts/<host>/` for genuine host differences.
Host templates inherit named blocks; do not edit generated copies under `integrations/`.
`resources.json` lists the complete packaged resource set, including linked references.
`legacy.json` is a frozen migration record of pristine pre-marker resources, not a second editable guidance source.

Run `uv run python scripts/integration_guidance_skills.py --write` (or `make integration-guidance`),
then `uv run python scripts/integration_guidance_skills.py --check-generated`.
The latter runs in `make check`, reports drift without writing, and never runs model evaluation.
Running the generator again with unchanged inputs preserves file bytes and mtimes.

`src/powercontext/cli/guidance.py` owns distribution paths, installer entry points and the permitted
path variables. Supported capabilities come from `integrations/capabilities.toml`; capability removal,
missing resolver scripts and unknown template variables fail generation. The portable agent-plugin
and MiniMax MCP packages use the Server MCP toolset as a content contract, not a native-host qualification.
DSH provides runtime Skills and is intentionally outside this file-generation surface.

The managed range starts at `<!-- POWERCONTEXT-GUIDANCE:START -->` and ends at
`<!-- POWERCONTEXT-GUIDANCE:END -->`. For `SKILL.md`, the start marker is the YAML comment
`# POWERCONTEXT-GUIDANCE:START` immediately after the opening `---`, so frontmatter remains valid
and the discovery description is updated with the body. Text outside the range is preserved byte-for-byte.
Duplicate, incomplete or reversed markers fail before writing any generated file.

The installation helpers for WorkBuddy/OpenCode and the staged Hermes/ZCode upgrades preserve these
ranges and additional local Skill files. An unchanged legacy document can be migrated automatically.
An edited document without markers is rejected rather than overwritten: identify the package-owned
range and add the matching markers, leaving personal notes outside. Host-managed package installation
remains subject to that host's behavior; generation tests do not certify native discovery or execution.

The shared embedding-cost note is an intentional content addition. Claude Code's resolver accepts the
host-provided plugin root, verifies its helper, and falls back to the installed Skill's plugin root when
the environment variable is unavailable. Other existing host instructions are retained through template inheritance.

After committing a Claude Code Skill change, refresh its pinned evaluation copy with
`uv run python evaluation/skill-up/sync_skill.py --revision HEAD` and run that script with `--check`.
The offline evaluation checks do not replace a new authenticated model run for the new pin.
