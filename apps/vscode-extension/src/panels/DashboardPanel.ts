import * as vscode from 'vscode';
import * as path from 'path';

export class DashboardPanel {
  public static currentPanel: DashboardPanel | undefined;
  private readonly _panel: vscode.WebviewPanel;
  private readonly _extensionUri: vscode.Uri;
  private _disposables: vscode.Disposable[] = [];

  public static createOrShow(extensionUri: vscode.Uri): void {
    const column = vscode.window.activeTextEditor?.viewColumn ?? vscode.ViewColumn.One;
    if (DashboardPanel.currentPanel) {
      DashboardPanel.currentPanel._panel.reveal(column);
      return;
    }
    const panel = vscode.window.createWebviewPanel(
      'powermemDashboard',
      'PowerMem Dashboard',
      column,
      { enableScripts: true, localResourceRoots: [vscode.Uri.file(path.join(extensionUri.fsPath, 'media'))] }
    );
    DashboardPanel.currentPanel = new DashboardPanel(panel, extensionUri);
  }

  private constructor(panel: vscode.WebviewPanel, extensionUri: vscode.Uri) {
    this._panel = panel;
    this._extensionUri = extensionUri;
    this._panel.onDidDispose(() => this.dispose(), null, this._disposables);
    this._panel.webview.html = this.getHtml();
    this._panel.webview.onDidReceiveMessage(
      (msg: { command: string }) => {
        switch (msg.command) {
          case 'quickNote':
            vscode.commands.executeCommand('powermem.quickNote');
            break;
          case 'query':
            vscode.commands.executeCommand('powermem.queryMemories');
            break;
          case 'settings':
            vscode.commands.executeCommand('powermem.setup');
            break;
        }
      },
      null,
      this._disposables
    );
  }

  public dispose(): void {
    DashboardPanel.currentPanel = undefined;
    this._panel.dispose();
    this._disposables.forEach((d) => d.dispose());
  }

  private getHtml(): string {
    return `<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>PowerMem</title>
  <style>
    body { font-family: var(--vscode-font-family); padding: 1rem; color: var(--vscode-foreground); }
    h1 { font-size: 1.2rem; }
    .btn { display: inline-block; margin: 0.25rem 0.25rem 0 0; padding: 0.5rem 1rem; background: var(--vscode-button-background); color: var(--vscode-button-foreground); border: none; cursor: pointer; border-radius: 4px; }
    .btn:hover { background: var(--vscode-button-hoverBackground); }
    p { margin: 0.5rem 0; }
  </style>
</head>
<body>
  <h1>PowerMem</h1>
  <p>Intelligent memory for AI assistants. Use the commands below or the status bar.</p>
  <button class="btn" onclick="vscode.postMessage({ command: 'query' })">Query memories</button>
  <button class="btn" onclick="vscode.postMessage({ command: 'quickNote' })">Quick note</button>
  <button class="btn" onclick="vscode.postMessage({ command: 'settings' })">Setup</button>
  <script>
    const vscode = acquireVsCodeApi();
  </script>
</body>
</html>`;
  }
}
