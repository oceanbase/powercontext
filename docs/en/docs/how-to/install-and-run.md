---
title: Install and run
description: Install PowerContext from Git and run the local Server.
---

# Install and run

## Install the application

Install `uv`, then install PowerContext directly from a Git ref:

```bash
uv tool install "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@main"
```

This works on macOS and Linux and does not require a user-managed repository checkout. Git uses its normal credential
configuration, including credential helpers and SSH settings. For an SSH-based install, replace the HTTPS URL with the
Git URL approved for your environment.

To install a tested branch or tag, replace `main` after the final `@`. Use the same ref when configuring integrations:

```bash
powercontext setup codex --source oceanbase/powercontext --ref <ref>
```

## Run the local Server

```bash
powercontext server run
```

With no environment variables, the Server:

- binds to `127.0.0.1:8000`;
- enables Streamable HTTP MCP at `/mcp`;
- creates a persistent SQLite database in the operating system's user data directory;
- supports explicit Memory operations without an inference provider.

`Ctrl-C` performs a clean shutdown. Restarting the command reopens the same database.

## Verify the installation

```bash
powercontext doctor
powercontext ready
powercontext capabilities
```

`doctor` checks the installed package, Codex plugin, and Server readiness. The content commands exercise the public
HTTP SDK path.

## Update or replace an installation

To replace the installed tool with a chosen ref:

```bash
uv tool install --force "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@<ref>"
powercontext setup codex --source oceanbase/powercontext --ref <ref>
```

Restart the Server and open a new Codex session after updating. Existing SQLite data remains in the user data
directory unless `POWERCONTEXT_HOME` or the database URL changes.

## Install a Python role

An application that imports the async Client SDK should add it to that application's environment:

```bash
uv add "powercontext[client] @ git+https://github.com/oceanbase/powercontext.git@main"
```

Use `builtin` for in-process Python composition, `server` for the service, `client` for the Python SDK, or `cli` for
the Server-backed command line.
An extra that is only present in the isolated `uv tool` environment is not importable by an unrelated Python project.
