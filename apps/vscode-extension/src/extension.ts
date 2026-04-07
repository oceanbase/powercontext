import * as vscode from 'vscode';
import { writeCursorConfig } from './writers/cursor';
import { writeClaudeConfig } from './writers/claude';
import { writeCodexConfig } from './writers/codex';
import { writeWindsurfConfig } from './writers/windsurf';
import { writeCopilotConfig } from './writers/copilot';
import { DashboardPanel } from './panels/DashboardPanel';
import { checkHealth } from './utils/health';
import { searchMemories, addMemory } from './api/client';
import type { SearchResultItem } from './api/types';
import { registerChatParticipant } from './chat/participant';

let backendUrl = 'http://localhost:8000';
let apiKey: string | undefined;
let statusBar: vscode.StatusBarItem;
let useMCP = true;
let mcpServerPath = '';
let isEnabled = true;
let userId = '';
let autoCaptureOnSave = false;
let autoCaptureInclude = '**/*.md,**/*.txt,**/docs/**';
let autoCaptureMaxChars = 8000;
let chatAutoSummarizeTurns = 10;
let chatAutoRetrieve = true;
let seamlessMode = true;

function getUseMCPFromConfig(config: vscode.WorkspaceConfiguration): boolean {
  const mode = config.get<'http' | 'mcp'>('connectionMode');
  if (mode !== undefined) return mode === 'mcp';
  return config.get<boolean>('useMCP') ?? true;
}

/** Simple glob match for auto-capture include (e.g. .md, docs/). Comma-separated patterns. */
function matchesAutoCaptureInclude(filePath: string, includePattern: string): boolean {
  const patterns = includePattern.split(',').map((p) => p.trim()).filter(Boolean);
  if (patterns.length === 0 || patterns.includes('**') || patterns.includes('*')) return true;
  const normalized = filePath.replace(/\\/g, '/');
  for (const p of patterns) {
    if (p.endsWith('/**')) {
      const segment = p.replace(/^\*\*\//, '').replace(/\/\*\*$/, '');
      if (segment && normalized.includes('/' + segment + '/')) return true;
    } else if (p.startsWith('**/*.')) {
      const ext = p.slice(5);
      if (normalized.endsWith('.' + ext)) return true;
    }
  }
  return false;
}

function getUserId(context: vscode.ExtensionContext, config: vscode.WorkspaceConfiguration): string {
  const configured = config.get<string>('userId');
  if (configured) return configured;
  let persisted = context.globalState.get<string>('powermem.userId');
  if (persisted) return persisted;
  const machineId = vscode.env.machineId;
  const user = process.env.USERNAME || process.env.USER || 'user';
  persisted = `${user}-${machineId.substring(0, 8)}`;
  context.globalState.update('powermem.userId', persisted);
  return persisted;
}

function updateStatusBar(state: 'active' | 'disconnected' | 'disabled'): void {
  const icons = {
    active: '$(database) PowerMem',
    disconnected: '$(warning) PowerMem',
    disabled: '$(circle-slash) PowerMem',
  };
  statusBar.text = icons[state];
  statusBar.tooltip = state === 'active' ? 'PowerMem connected. Click for menu.' : state === 'disconnected' ? 'PowerMem disconnected. Click to setup.' : 'PowerMem disabled.';
}

async function autoLinkAll(): Promise<void> {
  try {
    await writeCursorConfig(backendUrl, apiKey, useMCP, mcpServerPath || undefined);
    await writeClaudeConfig(backendUrl, apiKey, useMCP, mcpServerPath || undefined);
    await writeCodexConfig(backendUrl, apiKey, useMCP, mcpServerPath || undefined);
    await writeWindsurfConfig(backendUrl, apiKey, useMCP, mcpServerPath || undefined);
    await writeCopilotConfig(backendUrl, apiKey, useMCP, mcpServerPath || undefined);
    vscode.window.showInformationMessage(`PowerMem linked to AI tools (${useMCP ? 'MCP' : 'HTTP'})`);
  } catch (e) {
    console.error('PowerMem auto-link failed:', e);
    vscode.window.showErrorMessage(`PowerMem link failed: ${e}`);
  }
}

function formatMemories(results: SearchResultItem[]): string {
  let out = '# PowerMem Search Results\n\n';
  if (results.length === 0) return out + 'No memories found.\n';
  for (const r of results) {
    out += `## ${r.memory_id}\n**Score:** ${r.score ?? 'N/A'}\n${r.content}\n\n`;
  }
  return out;
}

async function showMenu(): Promise<void> {
  if (!isEnabled) {
    const choice = await vscode.window.showQuickPick(
      [
        { label: '$(check) Enable PowerMem', action: 'enable' },
        { label: '$(gear) Setup', action: 'setup' },
      ],
      { placeHolder: 'PowerMem is disabled' }
    );
    if (!choice) return;
    if (choice.action === 'enable') {
      await vscode.workspace.getConfiguration('powermem').update('enabled', true, vscode.ConfigurationTarget.Global);
      vscode.window.showInformationMessage('PowerMem enabled. Reload window to apply.');
      return;
    }
    if (choice.action === 'setup') {
      await showSetup();
    }
    return;
  }

  const items = [
    { label: '$(link) Link to AI tools', action: 'link' },
    { label: '$(search) Query memories', action: 'query' },
    { label: '$(add) Add selection to memory', action: 'add' },
    { label: '$(pencil) Quick note', action: 'note' },
    { label: '$(dashboard) Dashboard', action: 'dashboard' },
    { label: useMCP ? '$(server-process) Switch to HTTP' : '$(link) Switch to MCP', action: 'toggleMcp' },
    { label: '$(gear) Setup', action: 'setup' },
    { label: '$(refresh) Reconnect', action: 'reconnect' },
    { label: '$(circle-slash) Disable', action: 'disable' },
  ];
  const choice = await vscode.window.showQuickPick(items, { placeHolder: 'PowerMem' });
  if (!choice) return;
  switch (choice.action) {
    case 'link':
      await autoLinkAll();
      break;
    case 'query':
      vscode.commands.executeCommand('powermem.queryMemories');
      break;
    case 'add':
      vscode.commands.executeCommand('powermem.addSelectionToMemory');
      break;
    case 'note':
      vscode.commands.executeCommand('powermem.quickNote');
      break;
    case 'dashboard':
      vscode.commands.executeCommand('powermem.dashboard');
      break;
    case 'toggleMcp':
      useMCP = !useMCP;
      await vscode.workspace.getConfiguration('powermem').update('connectionMode', useMCP ? 'mcp' : 'http', vscode.ConfigurationTarget.Global);
      await autoLinkAll();
      break;
    case 'setup':
      await showSetup();
      break;
    case 'reconnect':
      if (await checkHealth(backendUrl)) {
        await autoLinkAll();
        updateStatusBar('active');
        vscode.window.showInformationMessage('PowerMem reconnected.');
      } else {
        updateStatusBar('disconnected');
        vscode.window.showErrorMessage('Cannot connect to PowerMem backend.');
      }
      break;
    case 'disable':
      await vscode.workspace.getConfiguration('powermem').update('enabled', false, vscode.ConfigurationTarget.Global);
      isEnabled = false;
      updateStatusBar('disabled');
      vscode.window.showInformationMessage('PowerMem disabled.');
      break;
  }
}

async function showSetup(): Promise<void> {
  const config = vscode.workspace.getConfiguration('powermem');
  const items = [
    { label: '$(server) Change backend URL', action: 'url', description: backendUrl },
    { label: '$(key) Set API key', action: 'apikey' },
    { label: '$(file-code) Set MCP server path', action: 'mcppath', description: mcpServerPath || '(use backend URL /mcp)' },
    { label: '$(debug-restart) Test connection', action: 'test' },
    { label: isEnabled ? '$(circle-slash) Disable' : '$(check) Enable', action: 'toggleEnabled' },
  ];
  const choice = await vscode.window.showQuickPick(items, { placeHolder: 'PowerMem Setup' });
  if (!choice) return;
  switch (choice.action) {
    case 'url': {
      const url = await vscode.window.showInputBox({ prompt: 'PowerMem backend URL', value: backendUrl, placeHolder: 'http://localhost:8000' });
      if (url) {
        await config.update('backendUrl', url, vscode.ConfigurationTarget.Global);
        backendUrl = url;
        if (await checkHealth(backendUrl)) {
          await autoLinkAll();
          updateStatusBar('active');
        }
        vscode.window.showInformationMessage('Backend URL updated.');
      }
      break;
    }
    case 'apikey': {
      const key = await vscode.window.showInputBox({ prompt: 'API key (empty if none)', password: true, value: apiKey ?? '' });
      await config.update('apiKey', key ?? '', vscode.ConfigurationTarget.Global);
      apiKey = key || undefined;
      vscode.window.showInformationMessage('API key saved.');
      break;
    }
    case 'mcppath': {
      const path = await vscode.window.showInputBox({ prompt: 'MCP server path/command (empty = use URL/mcp)', value: mcpServerPath, placeHolder: 'uvx' });
      await config.update('mcpServerPath', path ?? '', vscode.ConfigurationTarget.Global);
      mcpServerPath = path ?? '';
      vscode.window.showInformationMessage('MCP path updated.');
      break;
    }
    case 'test':
      if (await checkHealth(backendUrl)) {
        vscode.window.showInformationMessage('PowerMem connection OK.');
        updateStatusBar('active');
      } else {
        vscode.window.showErrorMessage('PowerMem connection failed.');
        updateStatusBar('disconnected');
      }
      break;
    case 'toggleEnabled':
      isEnabled = !isEnabled;
      await config.update('enabled', isEnabled, vscode.ConfigurationTarget.Global);
      if (isEnabled) {
        if (await checkHealth(backendUrl)) {
          await autoLinkAll();
          updateStatusBar('active');
        } else updateStatusBar('disconnected');
      } else updateStatusBar('disabled');
      vscode.window.showInformationMessage(isEnabled ? 'PowerMem enabled.' : 'PowerMem disabled.');
      break;
  }
}

export function activate(context: vscode.ExtensionContext): void {
  const config = vscode.workspace.getConfiguration('powermem');
  isEnabled = config.get<boolean>('enabled') ?? true;
  backendUrl = config.get<string>('backendUrl') || 'http://localhost:8000';
  apiKey = config.get<string>('apiKey') || undefined;
  useMCP = getUseMCPFromConfig(config);
  mcpServerPath = config.get<string>('mcpServerPath') || '';
  seamlessMode = config.get<boolean>('seamlessMode') ?? true;
  // In seamless mode, default auto-capture on save to true so extraction is automatic
  const explicitAutoCapture = config.inspect<boolean>('autoCapture.onSave');
  const explicitOnSave = explicitAutoCapture?.workspaceValue ?? explicitAutoCapture?.globalValue;
  autoCaptureOnSave = explicitOnSave !== undefined ? explicitOnSave : seamlessMode;
  autoCaptureInclude = config.get<string>('autoCapture.include') ?? '**/*.md,**/*.txt,**/docs/**';
  autoCaptureMaxChars = Math.max(500, config.get<number>('autoCapture.maxChars') ?? 8000);
  chatAutoSummarizeTurns = Math.max(0, config.get<number>('chat.autoSummarizeEveryNTurns') ?? 10);
  chatAutoRetrieve = config.get<boolean>('chat.autoRetrieve') ?? true;
  userId = getUserId(context, config);

  statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
  statusBar.command = 'powermem.statusBarClick';
  context.subscriptions.push(statusBar);

  context.subscriptions.push(
    vscode.commands.registerCommand('powermem.statusBarClick', () => showMenu())
  );

  registerChatParticipant(
    context,
    () => backendUrl,
    () => apiKey,
    () => userId,
    () => isEnabled,
    () => seamlessMode,
    () => chatAutoSummarizeTurns,
    () => chatAutoRetrieve
  );

  if (!isEnabled) {
    updateStatusBar('disabled');
    statusBar.show();
    return;
  }

  updateStatusBar('disconnected');
  statusBar.show();

  checkHealth(backendUrl).then(async (connected) => {
    if (connected) {
      await autoLinkAll();
      updateStatusBar('active');
    } else {
      updateStatusBar('disconnected');
    }
  });

  context.subscriptions.push(
    vscode.commands.registerCommand('powermem.queryMemories', async () => {
      const editor = vscode.window.activeTextEditor;
      const query = editor ? (editor.document.getText(editor.selection) || editor.document.getText()).trim() : '';
      const input = await vscode.window.showInputBox({ prompt: 'Search query', placeHolder: 'e.g. user preferences' });
      const q = query || (input ?? '');
      if (!q) return;
      await vscode.window.withProgress(
        { location: vscode.ProgressLocation.Notification, title: 'PowerMem: Searching...', cancellable: false },
        async () => {
          try {
            const data = await searchMemories(backendUrl, { query: q, user_id: userId || undefined, limit: 10 }, apiKey);
            const doc = await vscode.workspace.openTextDocument({ content: formatMemories(data.results), language: 'markdown' });
            await vscode.window.showTextDocument(doc);
          } catch (e) {
            vscode.window.showErrorMessage(`PowerMem search failed: ${e}`);
          }
        }
      );
    }),
    vscode.commands.registerCommand('powermem.addSelectionToMemory', async () => {
      const editor = vscode.window.activeTextEditor;
      if (!editor) {
        vscode.window.showErrorMessage('No active editor');
        return;
      }
      const selection = editor.document.getText(editor.selection);
      if (!selection.trim()) {
        vscode.window.showErrorMessage('Select text to add to memory');
        return;
      }
      await vscode.window.withProgress(
        { location: vscode.ProgressLocation.Notification, title: 'PowerMem: Saving...', cancellable: false },
        async () => {
          try {
            await addMemory(backendUrl, { content: selection, user_id: userId || undefined, metadata: { source: 'vscode', file: editor.document.uri.fsPath } }, apiKey);
            vscode.window.showInformationMessage('Selection added to PowerMem');
          } catch (e) {
            vscode.window.showErrorMessage(`PowerMem add failed: ${e}`);
          }
        }
      );
    }),
    vscode.commands.registerCommand('powermem.quickNote', async () => {
      const input = await vscode.window.showInputBox({ prompt: 'Quick note to remember', placeHolder: 'e.g. Use pnpm for this project' });
      if (!input?.trim()) return;
      try {
        await addMemory(backendUrl, { content: input.trim(), user_id: userId || undefined, metadata: { source: 'vscode', type: 'quick-note' } }, apiKey);
        vscode.window.showInformationMessage('Note added to PowerMem');
      } catch (e) {
        vscode.window.showErrorMessage(`PowerMem add failed: ${e}`);
      }
    }),
    vscode.commands.registerCommand('powermem.linkToAITools', () => autoLinkAll()),
    vscode.commands.registerCommand('powermem.setup', () => showSetup()),
    vscode.commands.registerCommand('powermem.dashboard', () => DashboardPanel.createOrShow(context.extensionUri))
  );

  context.subscriptions.push(
    vscode.workspace.onDidChangeConfiguration((e) => {
      if (!e.affectsConfiguration('powermem')) return;
      const c = vscode.workspace.getConfiguration('powermem');
      backendUrl = c.get<string>('backendUrl') || 'http://localhost:8000';
      apiKey = c.get<string>('apiKey') || undefined;
      useMCP = getUseMCPFromConfig(c);
      mcpServerPath = c.get<string>('mcpServerPath') || '';
      isEnabled = c.get<boolean>('enabled') ?? true;
      seamlessMode = c.get<boolean>('seamlessMode') ?? true;
      const explicitAutoCapture = c.inspect<boolean>('autoCapture.onSave');
      const explicitOnSave = explicitAutoCapture?.workspaceValue ?? explicitAutoCapture?.globalValue;
      autoCaptureOnSave = explicitOnSave !== undefined ? explicitOnSave : seamlessMode;
      autoCaptureInclude = c.get<string>('autoCapture.include') ?? '**/*.md,**/*.txt,**/docs/**';
      autoCaptureMaxChars = Math.max(500, c.get<number>('autoCapture.maxChars') ?? 8000);
      chatAutoSummarizeTurns = Math.max(0, c.get<number>('chat.autoSummarizeEveryNTurns') ?? 10);
      chatAutoRetrieve = c.get<boolean>('chat.autoRetrieve') ?? true;
      // Re-link AI tools when connection/backend config changes so user does not need to click "Link to AI tools"
      if (
        isEnabled &&
        (e.affectsConfiguration('powermem.backendUrl') ||
          e.affectsConfiguration('powermem.connectionMode') ||
          e.affectsConfiguration('powermem.useMCP') ||
          e.affectsConfiguration('powermem.mcpServerPath'))
      ) {
        autoLinkAll().catch((err) => console.error('PowerMem auto re-link failed:', err));
      }
    })
  );

  // Optional: auto-add to memory on save (seamless write)
  context.subscriptions.push(
    vscode.workspace.onDidSaveTextDocument(async (doc) => {
      if (!isEnabled || !autoCaptureOnSave || doc.uri.scheme !== 'file') return;
      const path = doc.uri.fsPath;
      if (!matchesAutoCaptureInclude(path, autoCaptureInclude)) return;
      const text = doc.getText();
      if (!text.trim()) return;
      const content = text.length > autoCaptureMaxChars ? text.slice(0, autoCaptureMaxChars) + '\n…' : text;
      try {
        await addMemory(backendUrl, {
          content,
          user_id: userId || undefined,
          metadata: { source: 'vscode', type: 'auto-save', file: path },
        }, apiKey);
      } catch {
        // Silent fail to avoid interrupting the user
      }
    })
  );
}

export function deactivate(): void {}
