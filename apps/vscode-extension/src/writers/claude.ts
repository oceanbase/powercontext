import * as fs from 'fs';
import * as path from 'path';
import * as os from 'os';

export interface ClaudeConfig {
  mcpServers?: {
    powermem?: {
      command?: string;
      args?: string[];
      env?: Record<string, string>;
      url?: string;
    };
  };
}

export function generateClaudeConfig(
  backendUrl: string,
  apiKey?: string,
  useMCP = true,
  mcpServerPath?: string
): ClaudeConfig {
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
