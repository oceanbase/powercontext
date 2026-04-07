import * as fs from 'fs';
import * as path from 'path';
import * as os from 'os';

export interface CopilotConfig {
  name: string;
  type: string;
  endpoint?: string;
  authentication?: { type: string; header: string };
  mcpServer?: { command: string; args: string[]; env?: Record<string, string> };
  url?: string;
}

export function generateCopilotConfig(
  backendUrl: string,
  apiKey?: string,
  useMCP = true,
  mcpServerPath?: string
): CopilotConfig {
  const base = backendUrl.replace(/\/+$/, '');
  if (useMCP) {
    const config: CopilotConfig = {
      name: 'PowerMem',
      type: 'mcp',
      mcpServer: { command: 'uvx', args: ['powermem-mcp', 'stdio'] },
    };
    if (mcpServerPath) {
      config.mcpServer = { command: 'uvx', args: ['powermem-mcp', 'stdio'], env: apiKey ? { POWERMEM_API_KEY: apiKey } : undefined };
    } else {
      (config as CopilotConfig & { url: string }).url = `${base}/mcp`;
    }
    return config;
  }
  const c: CopilotConfig = {
    name: 'PowerMem',
    type: 'context_provider',
    endpoint: `${base}/api/v1/memories/search`,
  };
  if (apiKey) c.authentication = { type: 'header', header: `X-API-Key: ${apiKey}` };
  return c;
}

export async function writeCopilotConfig(
  backendUrl: string,
  apiKey?: string,
  useMCP = true,
  mcpServerPath?: string
): Promise<string> {
  const copilotDir = path.join(os.homedir(), '.github', 'copilot');
  const configFile = path.join(copilotDir, 'powermem.json');
  if (!fs.existsSync(copilotDir)) {
    fs.mkdirSync(copilotDir, { recursive: true });
  }
  const config = generateCopilotConfig(backendUrl, apiKey, useMCP, mcpServerPath);
  fs.writeFileSync(configFile, JSON.stringify(config, null, 2));
  return configFile;
}
