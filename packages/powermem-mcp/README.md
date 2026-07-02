# powermem-mcp

Thin wrapper package for launching the PowerMem MCP server with `uvx`.

This package intentionally uses the same version as the main
[`powermem`](https://pypi.org/project/powermem/) package. For example,
`powermem-mcp==1.1.6` depends on `powermem[server,seekdb]==1.1.6`.

Use this package when an MCP client needs a zero-install command such as:

```bash
uvx powermem-mcp
```

## Usage

```bash
uvx powermem-mcp                  # streamable HTTP on :8848
uvx powermem-mcp stdio            # stdio
uvx powermem-mcp streamable-http 9000
uvx powermem-mcp sse 8848
```

If `uv` has cached an older tool environment, refresh explicitly:

```bash
uvx --refresh --upgrade powermem-mcp
```

## Development

Do not publish this package with a version that differs from the root
`powermem` package. The release flow should publish `powermem` first, then this
wrapper package for the same version.

See the [main repository](https://github.com/oceanbase/powermem) for full documentation.
