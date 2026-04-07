import * as fs from 'fs';
import * as path from 'path';
import * as os from 'os';

export interface CodexConfig {
  contextProviders?: Record<string, unknown>;
  mcpServers?: {
    powermem?: {
      url?: string;
      command?: string;
      args?: string[];
      env?: Record<string, string>;
    };
  };
}

export function generateCodexConfig(
  backendUrl: string,
  apiKey?: string,
  useMCP = true,
  mcpServerPath?: string
): CodexConfig {
  const base = backendUrl.replace(/\/+$/, '');
  if (useMCP) {
    const config: CodexConfig = {
      mcpServers: {
        powermem: mcpServerPath
          ? { command: 'uvx', args: ['powermem-mcp', 'stdio'], env: apiKey ? { POWERMEM_API_KEY: apiKey } : undefined }
          : { url: `${base}/mcp` },
      },
    };
    return config;
  }
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
