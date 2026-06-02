/** Shared MCP server entry for stdio (uvx) vs streamable HTTP URL. */
export interface McpServerEntry {
  url?: string;
  command?: string;
  args?: string[];
  env?: Record<string, string>;
}

export function normalizeBackendUrl(backendUrl: string): string {
  return backendUrl.replace(/\/+$/, '');
}

/**
 * Build the powermem MCP server entry: local stdio when mcpServerPath is set,
 * otherwise remote `{backendUrl}/mcp`. Returns null when useMCP is false.
 */
export function buildMcpEntry(
  backendUrl: string,
  apiKey?: string,
  useMCP = true,
  mcpServerPath?: string
): McpServerEntry | null {
  if (!useMCP) {
    return null;
  }
  if (mcpServerPath) {
    return {
      command: 'uvx',
      args: ['powermem-mcp', 'stdio'],
      env: apiKey ? { POWERMEM_API_KEY: apiKey } : undefined,
    };
  }
  const base = normalizeBackendUrl(backendUrl);
  return { url: `${base}/mcp` };
}
