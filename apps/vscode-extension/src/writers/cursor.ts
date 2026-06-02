import * as fs from 'fs';
import * as path from 'path';
import * as os from 'os';
import { buildMcpEntry, type McpServerEntry } from './mcp-entry';

/** Cursor: ~/.cursor/mcp.json (global) or project .cursor/mcp.json. We write global. */
export interface CursorMcpConfig {
  mcpServers?: {
    powermem?: McpServerEntry;
  };
}

export function generateCursorConfig(
  backendUrl: string,
  apiKey?: string,
  useMCP = true,
  mcpServerPath?: string
): CursorMcpConfig {
  const entry = buildMcpEntry(backendUrl, apiKey, useMCP, mcpServerPath);
  if (entry) {
    return { mcpServers: { powermem: entry } };
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
