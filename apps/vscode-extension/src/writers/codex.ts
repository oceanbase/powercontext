import * as fs from 'fs';
import * as path from 'path';
import * as os from 'os';
import { buildMcpEntry, normalizeBackendUrl, type McpServerEntry } from './mcp-entry';

export interface CodexConfig {
  contextProviders?: Record<string, unknown>;
  mcpServers?: {
    powermem?: McpServerEntry;
  };
}

export function generateCodexConfig(
  backendUrl: string,
  apiKey?: string,
  useMCP = true,
  mcpServerPath?: string
): CodexConfig {
  const entry = buildMcpEntry(backendUrl, apiKey, useMCP, mcpServerPath);
  if (entry) {
    return { mcpServers: { powermem: entry } };
  }
  const base = normalizeBackendUrl(backendUrl);
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (apiKey) headers['X-API-Key'] = apiKey;
  return {
    contextProviders: {
      powermem: {
        enabled: true,
        endpoint: `${base}/api/v1/memories/search`,
        method: 'POST',
        headers,
        queryField: 'query',
      },
    },
  };
}

export async function writeCodexConfig(
  backendUrl: string,
  apiKey?: string,
  useMCP = true,
  mcpServerPath?: string
): Promise<string> {
  const codexDir = path.join(os.homedir(), '.codex');
  const configFile = path.join(codexDir, 'context.json');
  if (!fs.existsSync(codexDir)) {
    fs.mkdirSync(codexDir, { recursive: true });
  }
  let existing: CodexConfig = {};
  if (fs.existsSync(configFile)) {
    try {
      existing = JSON.parse(fs.readFileSync(configFile, 'utf8')) as CodexConfig;
    } catch {
      // ignore
    }
  }
  const generated = generateCodexConfig(backendUrl, apiKey, useMCP, mcpServerPath);
  const merged: CodexConfig = {
    contextProviders: { ...existing.contextProviders, ...generated.contextProviders },
    mcpServers: { ...existing.mcpServers, ...generated.mcpServers },
  };
  fs.writeFileSync(configFile, JSON.stringify(merged, null, 2));
  return configFile;
}
