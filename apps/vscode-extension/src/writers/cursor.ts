import * as fs from 'fs';
import * as path from 'path';
import * as os from 'os';

/** Cursor: ~/.cursor/mcp.json (global) or project .cursor/mcp.json. We write global. */
export interface CursorMcpConfig {
  mcpServers?: {
    powermem?: {
      url?: string;
      command?: string;
      args?: string[];
      env?: Record<string, string>;
    };
  };
}

export function generateCursorConfig(
  backendUrl: string,
  apiKey?: string,
  useMCP = true,
  mcpServerPath?: string
): CursorMcpConfig {
  const base = backendUrl.replace(/\/+$/, '');
  if (useMCP) {
    if (mcpServerPath) {
      return {
        mcpServers: {
          powermem: {
            command: 'uvx',
            args: ['powermem-mcp', 'stdio'],
            env: apiKey ? { POWERMEM_API_KEY: apiKey } : undefined,
          },
        },
      };
    }
    return {
      mcpServers: {
        powermem: { url: `${base}/mcp` },
      },
    };
  }
  // HTTP mode: do not add MCP config; caller will remove existing powermem entry
  return { mcpServers: {} };
}

export async function writeCursorConfig(
  backendUrl: string,
  apiKey?: string,
  useMCP = true,
  mcpServerPath?: string
): Promise<string> {
  const cursorDir = path.join(os.homedir(), '.cursor');
  const configFile = path.join(cursorDir, 'mcp.json');
  if (!fs.existsSync(cursorDir)) {
    fs.mkdirSync(cursorDir, { recursive: true });
  }
  let existing: CursorMcpConfig = {};
  if (fs.existsSync(configFile)) {
    try {
      existing = JSON.parse(fs.readFileSync(configFile, 'utf8')) as CursorMcpConfig;
    } catch {
      // ignore
    }
  }
  const generated = generateCursorConfig(backendUrl, apiKey, useMCP, mcpServerPath);
  const merged: CursorMcpConfig = {
    mcpServers: { ...existing.mcpServers, ...generated.mcpServers },
  };
  if (!useMCP && merged.mcpServers) {
    delete merged.mcpServers.powermem;
  }
  fs.writeFileSync(configFile, JSON.stringify(merged, null, 2));
  return configFile;
}
