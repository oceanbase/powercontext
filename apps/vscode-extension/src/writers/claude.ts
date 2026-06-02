import * as fs from 'fs';
import * as path from 'path';
import * as os from 'os';
import { buildMcpEntry, type McpServerEntry } from './mcp-entry';

export interface ClaudeConfig {
  mcpServers?: {
    powermem?: McpServerEntry;
  };
}

export function generateClaudeConfig(
  backendUrl: string,
  apiKey?: string,
  useMCP = true,
  mcpServerPath?: string
): ClaudeConfig {
  const entry = buildMcpEntry(backendUrl, apiKey, useMCP, mcpServerPath);
  if (entry) {
    return { mcpServers: { powermem: entry } };
  }
  // HTTP mode: do not write MCP config so the client does not call /mcp
  return { mcpServers: {} };
}

export async function writeClaudeConfig(
  backendUrl: string,
  apiKey?: string,
  useMCP = true,
  mcpServerPath?: string
): Promise<string> {
  const claudeDir = path.join(os.homedir(), '.claude', 'providers');
  const configFile = path.join(claudeDir, 'powermem.json');
  if (!fs.existsSync(claudeDir)) {
    fs.mkdirSync(claudeDir, { recursive: true });
  }
  const config = generateClaudeConfig(backendUrl, apiKey, useMCP, mcpServerPath);
  fs.writeFileSync(configFile, JSON.stringify(config, null, 2));
  return configFile;
}
