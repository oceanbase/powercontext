# PowerMem CLI Usage Guide

This guide describes how to use the PowerMem Command Line Interface (CLI) introduced in PowerMem 1.0.0. The CLI provides a full set of memory operations, configuration management, backup/restore, and an interactive shell—all from the terminal.

## Table of Contents

- [Installation and Invocation](#installation-and-invocation)
- [Global Options](#global-options)
- [Command Overview](#command-overview)
- [Memory Commands](#memory-commands)
- [Configuration Commands](#configuration-commands)
- [Statistics](#statistics)
- [Management Commands](#management-commands)
- [Interactive Shell](#interactive-shell)
- [Shell Completion](#shell-completion)

---

## Installation and Invocation

After installing PowerMem, the CLI is available as:

- **`pmem`** – primary entry point (when installed as a console script)
- **`powermem-cli`** – alternative entry point

```bash
# Ensure PowerMem is installed
pip install powermem

# Check version and help
pmem --version
pmem --help
```

If no subcommand is given, the CLI prints "Missing command." and shows the main help.

---

## Global Options

These options belong to the root command. **`--env-file` / `-f` and `--verbose` / `-v` must appear before the subcommand name** (standard Click group options). **`--json` / `-j` is also accepted on many subcommands** after the subcommand—see each command’s help.

| Option | Short | Description |
|--------|-------|-------------|
| `--env-file PATH` | `-f` | Path to a `.env` configuration file. Overrides the default (e.g. `./.env`). |
| `--json` | `-j` | Output results in JSON format. |
| `--verbose` | `-v` | Enable verbose output (e.g. stack traces on errors). |
| `--install-completion SHELL` | — | Install shell completion for `bash`, `zsh`, `fish`, or `powershell`. |
| `--version` | — | Show CLI version. |
| `--help` | `-h` | Show help. |

### Global env file placement

Options **`--env-file` / `-f`** at the root:

- **Placement:** Always **before** the subcommand, e.g. `pmem -f ./.env.staging memory list`, not `pmem memory list -f ./.env` (the latter is invalid for the global option).
- **Scope:** The same file is used for **`memory`**, **`config`**, **`stats`**, **`manage`**, and **`shell`** for that invocation (same behavior as setting `POWERMEM_ENV_FILE`).
- **`config validate` and `config init`** also accept `--env-file` / `-f` **on the subcommand**:
  - **validate:** file to validate (if omitted, falls back to the global `--env-file`, then the default discovered `.env`).
  - **init:** target file to write (if omitted, falls back to the global `--env-file`, then the default path).
- **`memory search`:** After `search`, **`-f` means `--filters`** (JSON filters), not the env file. To point at a `.env` for that run, use **`pmem -f path/to/.env memory search "…"`** or **`pmem --env-file path/to/.env memory search "…"`**.

**Examples:**

```bash
pmem -f .env.production memory list
pmem --env-file .env.production config show
pmem --json stats
pmem -v memory add "User prefers dark mode" --user-id user123
pmem --install-completion bash
```

---

## Command Overview

| Command Group | Subcommands | Description |
|---------------|-------------|-------------|
| **memory** | add, search, get, update, delete, list, delete-all | CRUD and search for memories. |
| **config** | show, validate, test, init | View, validate, test, and initialize configuration. |
| **stats** | (none) | Display memory statistics. |
| **manage** | backup, restore, cleanup, migrate | Backup, restore, cleanup, and migrate data. |
| **shell** | (none) | Start interactive mode (REPL). |

---

## Memory Commands

All memory commands run under the `memory` group and use the same backend as the Python SDK (same config and storage). To use a non-default `.env`, pass **global** `--env-file` / `-f` **before** `memory` (see [Global env file placement](#global-env-file-placement)).

### `pmem memory add CONTENT`

Add a new memory. Content can be a single fact or short description; with inference enabled (default), the system may deduplicate or merge with existing memories.

**Arguments:**

- `CONTENT` (required): The memory content (e.g. a sentence or short paragraph).

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--user-id USER_ID` | `-u` | User ID for the memory. |
| `--agent-id AGENT_ID` | `-a` | Agent ID for the memory. |
| `--run-id RUN_ID` | `-r` | Run/session ID. |
| `--metadata JSON` | `-m` | Metadata as a JSON string, e.g. `'{"key": "value"}'`. |
| `--scope SCOPE` | — | One of: `private`, `agent_group`, `user_group`, `public`. |
| `--memory-type TYPE` | — | One of: `working`, `short_term`, `long_term`. |
| `--no-infer` | — | Disable intelligent inference (no dedup/merge). |
| `--json` | `-j` | Output in JSON. |

**Examples:**

```bash
pmem memory add "User prefers dark mode" --user-id user123
pmem memory add "API key is stored in vault" -m '{"category": "security"}'
pmem memory add "Meeting at 3pm Friday" -u user1 -a agent1 --no-infer
```

---

### `pmem memory search QUERY`

Search memories by semantic similarity to the given query.

**Arguments:**

- `QUERY` (required): Search query text.

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--user-id USER_ID` | `-u` | Filter by user ID. |
| `--agent-id AGENT_ID` | `-a` | Filter by agent ID. |
| `--run-id RUN_ID` | `-r` | Filter by run/session ID. |
| `--limit N` | `-l` | Maximum number of results (default: 10). |
| `--threshold T` | `-t` | Minimum similarity score (e.g. `0.3`). |
| `--filters JSON` | `-f` | Additional filters as JSON. |
| `--json` | `-j` | Output in JSON. |

**Note:** On this subcommand, **`-f` is `--filters`**, not the global env file. To use a specific `.env`, run `pmem -f /path/.env memory search "…"` (global option before `memory`).

**Examples:**

```bash
pmem memory search "user preferences" --user-id user123
pmem memory search "dark mode" -l 5 -j
pmem memory search "123" -t 0.3
pmem -f .env.production memory search "preferences" --user-id user123
```

---

### `pmem memory get MEMORY_ID`

Retrieve a single memory by its global ID. Optional `--user-id` / `--agent-id` enforce access control (memory is returned only if it belongs to that user/agent).

**Arguments:**

- `MEMORY_ID` (required): Numeric memory ID.

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--user-id USER_ID` | `-u` | User ID for access control. |
| `--agent-id AGENT_ID` | `-a` | Agent ID for access control. |
| `--json` | `-j` | Output in JSON. |

**Examples:**

```bash
pmem memory get 123456789
pmem memory get 123456789 --user-id user123
```

---

### `pmem memory update MEMORY_ID CONTENT`

Update an existing memory’s content (and optionally metadata).

**Arguments:**

- `MEMORY_ID` (required): Numeric memory ID.
- `CONTENT` (required): New content.

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--user-id USER_ID` | `-u` | User ID for access control. |
| `--agent-id AGENT_ID` | `-a` | Agent ID for access control. |
| `--metadata JSON` | `-m` | New metadata as JSON. |
| `--json` | `-j` | Output in JSON. |

**Examples:**

```bash
pmem memory update 123456789 "Updated content"
pmem memory update 123456789 "New content" -m '{"updated": true}'
```

---

### `pmem memory delete MEMORY_ID`

Delete a memory by ID. Prompts for confirmation unless `--yes` is used.

**Arguments:**

- `MEMORY_ID` (required): Numeric memory ID.

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--user-id USER_ID` | `-u` | User ID for access control. |
| `--agent-id AGENT_ID` | `-a` | Agent ID for access control. |
| `--yes` | `-y` | Skip confirmation. |

**Examples:**

```bash
pmem memory delete 123456789
pmem memory delete 123456789 --yes
```

---

### `pmem memory list`

List memories with optional filters, pagination, and sorting.

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--user-id USER_ID` | `-u` | Filter by user ID. |
| `--agent-id AGENT_ID` | `-a` | Filter by agent ID. |
| `--run-id RUN_ID` | `-r` | Filter by run ID. |
| `--limit N` | `-l` | Maximum results (default: 50). |
| `--offset N` | `-o` | Offset for pagination (default: 0). |
| `--sort-by FIELD` | `-s` | Sort by: `created_at`, `updated_at`, `id` (default: `created_at`). |
| `--order ORDER` | — | `asc` or `desc` (default: `desc`). |
| `--filters JSON` | `-f` | Additional filters as JSON. |
| `--json` | `-j` | Output in JSON. |

**Examples:**

```bash
pmem memory list --user-id user123
pmem memory list -l 20 -o 0
pmem memory list --sort-by created_at --order desc
```

---

### `pmem memory delete-all`

Delete all memories matching the given filters. **Irreversible.** Requires `--confirm` and an interactive confirmation.

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--user-id USER_ID` | `-u` | Filter by user ID. |
| `--agent-id AGENT_ID` | `-a` | Filter by agent ID. |
| `--run-id RUN_ID` | `-r` | Filter by run ID. |
| `--confirm` | — | **Required.** Acknowledge bulk deletion. |

**Examples:**

```bash
pmem memory delete-all --user-id user123 --confirm
pmem memory delete-all --run-id session1 --confirm
```

---

## Configuration Commands

Configuration commands use the same `.env`-based setup as the SDK. Use **global** `--env-file` / `-f` **before** `config` for any subcommand, or the subcommand’s own `--env-file` / `-f` on **`config validate`** and **`config init`** (see [Global env file placement](#global-env-file-placement)).

### `pmem config show`

Display current configuration (from the chosen `.env` file). Sensitive values (e.g. API keys, passwords) are masked unless `--show-secrets` is used.

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--section SECTION` | `-s` | Section to show: `llm`, `embedder`, `vector_store`, `graph_store`, `intelligent_memory`, `agent_memory`, `reranker`, or `all` (default). |
| `--show-secrets` | — | Show API keys and passwords (use with caution). |
| `--json` | `-j` | Output in JSON. |

**Examples:**

```bash
pmem config show
pmem config show --section llm
pmem config show -j
pmem -f .env.production config show
```

---

### `pmem config validate`

Validate the configuration file. Reports errors and optional warnings; with `--strict`, more checks are enforced.

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--env-file PATH` | `-f` | Path to `.env` file to validate. |
| `--strict` | — | Enable strict validation. |
| `--json` | `-j` | Output in JSON. |

**Examples:**

```bash
pmem config validate
pmem config validate -f .env.production
pmem config validate --strict
```

---

### `pmem config test`

Test connectivity for database, LLM, and embedder (using the current config).

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--component COMPONENT` | `-c` | One of: `database`, `llm`, `embedder`, `all` (default). |
| `--json` | `-j` | Output in JSON. |

**Examples:**

```bash
pmem config test
pmem config test -c database
pmem config test -c llm
```

---

### `pmem config init`

Run an interactive configuration wizard that creates or updates a `.env` file. Supports quickstart (minimal prompts) or custom (full) mode.

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--env-file PATH` | `-f` | Target `.env` file (default: auto-detected or `./.env`). |
| `--dry-run` | — | Show planned changes without writing. |
| `--test` / `--no-test` | — | Run validation and connectivity tests after writing (default: no). |
| `--component COMPONENT` | `-c` | When `--test` is used: `database`, `llm`, `embedder`, or `all`. |

**Examples:**

```bash
pmem config init
pmem config init -f .env
pmem config init --test --component database
```

---

## Statistics

### `pmem stats`

Display memory statistics (total counts, distribution by type, age, etc.). Optional filters apply to the same backend as other memory commands.

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--user-id USER_ID` | `-u` | Filter by user ID. |
| `--agent-id AGENT_ID` | `-a` | Filter by agent ID. |
| `--detailed` | `-d` | Show more detailed statistics. |
| `--json` | `-j` | Output in JSON. |

**Examples:**

```bash
pmem stats
pmem stats -u user123
pmem stats --agent-id agent1 -j
pmem stats --detailed
```

---

## Management Commands

### `pmem manage backup`

Export memories to a JSON file. Filters and limit control which memories are included.

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--output PATH` | `-o` | Output file (default: `powermem_backup_<timestamp>.json`). |
| `--user-id USER_ID` | `-u` | Filter by user ID. |
| `--agent-id AGENT_ID` | `-a` | Filter by agent ID. |
| `--run-id RUN_ID` | `-r` | Filter by run ID. |
| `--limit N` | `-l` | Maximum memories to export (default: 10000). |
| `--include-metadata` | — | Include metadata (default: true). |
| `--json` | `-j` | Output status in JSON. |

**Examples:**

```bash
pmem manage backup -o backup.json
pmem manage backup --user-id user123 -o user_backup.json
pmem manage backup -l 1000
```

---

### `pmem manage restore`

Import memories from a JSON backup file produced by `pmem manage backup`. Can override user/agent IDs and skip duplicates.

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--input PATH` | `-i` | **Required.** Input backup file. |
| `--user-id USER_ID` | `-u` | Override user ID for all restored memories. |
| `--agent-id AGENT_ID` | `-a` | Override agent ID for all restored memories. |
| `--dry-run` | — | Preview restore without writing. |
| `--skip-duplicates` | — | Skip memories that already exist (default: true). |
| `--json` | `-j` | Output in JSON. |

**Examples:**

```bash
pmem manage restore -i backup.json
pmem manage restore -i backup.json --dry-run
pmem manage restore -i backup.json -u new_user
```

---

### `pmem manage cleanup`

Remove or archive memories with low retention scores (Ebbinghaus-based). Use `--dry-run` to preview.

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--threshold T` | `-t` | Retention score below which to delete (default: 0.1). |
| `--archive-threshold T` | — | Retention score below which to archive (default: 0.3). |
| `--user-id USER_ID` | `-u` | Filter by user ID. |
| `--agent-id AGENT_ID` | `-a` | Filter by agent ID. |
| `--dry-run` | — | Preview only, no changes. |
| `--force` | `-f` | Skip confirmation. |
| `--json` | `-j` | Output in JSON. |

**Examples:**

```bash
pmem manage cleanup --dry-run
pmem manage cleanup --threshold 0.2
pmem manage cleanup -u user123 --force
```

---

### `pmem manage migrate`

Migrate data between stores (e.g. main store and sub-stores). Availability depends on the storage backend.

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--target-store INDEX` | `-t` | **Required.** Target sub-store index. |
| `--source-store INDEX` | `-s` | Source sub-store index (default: main store). |
| `--delete-source` | — | Delete from source after migration. |
| `--dry-run` | — | Preview only. |
| `--json` | `-j` | Output in JSON. |

**Examples:**

```bash
pmem manage migrate -t 0 --dry-run
pmem manage migrate -t 1 --delete-source
```

---

## Interactive Shell

### `pmem shell`

Start an interactive REPL (Read-Eval-Print Loop) for PowerMem. You can run memory and stats commands without typing `pmem memory` or `pmem stats` each time, and set default `user_id` / `agent_id` for the session.

**Commands inside the shell:**

| Command | Description |
|---------|-------------|
| `add <content> [--user-id id] [--agent-id id]` | Add a memory. |
| `search <query> [--user-id id] [--limit n] [--threshold t]` | Search memories. |
| `get <memory_id> [--user-id id]` | Get a memory by ID. |
| `update <memory_id> <content> [--user-id id]` | Update a memory. |
| `delete <memory_id> [--user-id id]` | Delete a memory. |
| `list [--user-id id] [--limit n]` | List memories. |
| `stats [--user-id id]` | Show statistics. |
| `set user <user_id>` | Set default user ID. |
| `set agent <agent_id>` | Set default agent ID. |
| `set json on\|off` | Enable/disable JSON output. |
| `show settings` | Show current session settings. |
| `clear` | Clear the screen. |
| `help` | Show help. |
| `exit`, `quit`, `q` | Exit the shell. |

**Example session:**

```bash
$ pmem shell

==================================================
  PowerMem Interactive Mode
==================================================
Type 'help' for available commands, 'exit' to quit

powermem> set user user123
powermem> add "User prefers dark mode"
powermem> search "preferences"
powermem> list --limit 10
powermem> exit
```

---

## Shell Completion

You can install tab-completion for `pmem` (and `powermem-cli`) so that subcommands and options are suggested on TAB.

**Install:**

```bash
# Bash
pmem --install-completion bash
# Then source your ~/.bashrc or open a new terminal.

# Zsh
pmem --install-completion zsh

# Fish
pmem --install-completion fish

# PowerShell
pmem --install-completion powershell
# Add the printed script to your $PROFILE to persist.
```

Bash/Zsh scripts are written under `~/.config/powermem/` and, if you confirm, a source line is added to your `~/.bashrc` or `~/.zshrc`. Fish completion is installed under `~/.config/fish/completions/pmem.fish`. PowerShell instructions are printed for you to add to your profile.

---

## Summary

- Use **`pmem`** (or **`powermem-cli`**) with **global options** placed **before** the subcommand: **`-f` / `--env-file`** (applies to all command groups for that run), **`-j` / `--json`**, **`-v` / `--verbose`**, plus **memory**, **config**, **stats**, **manage**, and **shell** as subcommands.
- **Memory operations**: `memory add/search/get/update/delete/list/delete-all` with filters and JSON output.
- **Configuration**: `config show/validate/test/init` for inspecting, validating, testing, and interactively creating `.env`.
- **Statistics**: `stats` with optional user/agent filters and `--detailed`.
- **Management**: `manage backup/restore/cleanup/migrate` for backup, restore, retention cleanup, and store migration.
- **Interactive use**: `pmem shell` for a REPL with session defaults and the same operations.
- **Completion**: `pmem --install-completion bash|zsh|fish|powershell` for TAB completion.

For configuration details (e.g. `.env` variables), see the [Configuration Guide](./0003-configuration.md). For SDK and API usage, see [Getting Started](./0001-getting_started.md) and the [API documentation](../api/overview.md).
