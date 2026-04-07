import * as fs from 'fs';
import * as path from 'path';
import * as os from 'os';

export interface WindsurfConfig {
  contextProvider?: string;
  api?: string;
  apiKey?: string;
  mcp?: { configPath?: string; url?: string };
}

export function generateWindsurfConfig(
  backendUrl: string,
  apiKey?: string,
  useMCP = true,
  mcpServerPath?: string
): WindsurfConfig {
  const base = backendUrl.replace(/\/+$/, '');
  if (useMCP) {
    if (mcpServerPath) {
      return { contextProvider: 'powermem-mcp', mcp: { configPath: mcpServerPath } };
    }
    return { contextProvider: 'powermem-mcp', mcp: { url: `${base}/mcp` } };
  }
  const config: WindsurfConfig = { contextProvider: 'powermem', api: `${base}/api/v1/memories/search` };
  if (apiKey) config.apiKey = apiKey;
  return config;
}

export async function writeWindsurfConfig(
  backendUrl: string,
  apiKey?: string,
  useMCP = true,
  mcpServerPath?: string
): Promise<string> {
  const windsurfDir = path.join(os.homedir(), '.windsurf', 'context');
  const configFile = path.join(windsurfDir, 'powermem.json');
  if (!fs.existsSync(windsurfDir)) {
    fs.mkdirSync(windsurfDir, { recursive: true });
  }
  const config = generateWindsurfConfig(backendUrl, apiKey, useMCP, mcpServerPath);
  fs.writeFileSync(configFile, JSON.stringify(config, null, 2));
  return configFile;
}
