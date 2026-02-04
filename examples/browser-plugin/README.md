# PowerMem Browser Extension

> Intelligent memory capture and retrieval for your browsing experience.

[![Version](https://img.shields.io/badge/version-0.1.0-blue.svg)](https://github.com/your-org/powermem-extension)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](./LICENSE)

## 📖 Introduction

PowerMem Browser Extension is an intelligent memory capture and retrieval browser extension that helps you better manage and utilize information during your browsing experience.

## ✨ Core Features

- 🚀 **Quick Capture** - Save selected text with one click
- 🔍 **Semantic Search** - AI-powered intelligent search
- 🧠 **Context Aware** - Smart recommendations based on current page
- 💼 **Multi-context Support** - Manage memories by work, personal, research, etc.

## 🎯 Installation

### Install from Chrome Web Store (Coming Soon)

1. Visit [Chrome Web Store](#)
2. Click "Add to Chrome"
3. Follow installation prompts to complete

### Install from Source

```bash
# Clone repository
git clone https://github.com/your-org/powermem-extension.git
cd powermem-extension/browser-plugin

# Install dependencies
npm install

# Build extension
npm run build
```

Then on Chrome extensions management page (`chrome://extensions/`):
1. Enable "Developer mode"
2. Click "Load unpacked"
3. Select the `dist` directory

## 🚀 Quick Start

### 1. Configure Server

1. Click extension icon
2. Go to "Settings" page
3. Enter PowerMem server address and API Key
4. Click "Test Connection" to verify configuration

### 2. Save Memory

- **Method 1**: Click save button that appears after selecting text
- **Method 2**: Right-click menu and select "Save to PowerMem"
- **Method 3**: Use keyboard shortcut `Ctrl+Shift+S` (Windows) or `Cmd+Shift+S` (Mac)

### 3. Search Memories

1. Click extension icon to open popup
2. Enter keywords in search box
3. View search results and click to see details

### 4. Use Sidebar

- Click "Open Sidebar" in extension icon menu
- View memory recommendations related to current page
- Quick access to recent memories

## 🔧 Development

### Development Environment

```bash
# Development mode (hot reload)
npm run dev

# Run tests
npm run test

# Run test coverage
npm run test:coverage

# E2E tests
npm run test:e2e

# Lint check
npm run lint
```

### Project Structure

```
browser-plugin/
├── src/
│   ├── background/       # Background scripts
│   ├── content/          # Content scripts
│   ├── popup/            # Popup interface
│   ├── sidebar/          # Sidebar interface
│   ├── options/          # Settings page
│   ├── services/         # Service layer
│   ├── store/            # State management
│   ├── shared/           # Shared components
│   └── utils/            # Utility functions
├── public/               # Static assets
├── tests/                # Test files
└── dist/                 # Build output
```

## 📚 Documentation

- [User Guide](./docs/USER_GUIDE.md)
- [Development Guide](./docs/DEVELOPMENT.md)
- [API Documentation](./docs/API.md)
- [FAQ](./docs/FAQ.md)

## 🔑 Keyboard Shortcuts

| Shortcut | Function |
|--------|------|
| `Ctrl+Shift+S` (Win) / `Cmd+Shift+S` (Mac) | Save selected text |
| `Alt+P` | Open popup |
| `Alt+S` | Focus search box |
| `Esc` | Close popup/modal |

## 🛠️ Tech Stack

- **Framework**: React 18 + TypeScript
- **Build Tool**: Vite + vite-plugin-web-extension
- **State Management**: Zustand
- **HTTP Client**: Axios
- **Testing**: Vitest + Playwright + Testing Library
- **Styling**: CSS Modules

# Start server:
```powershell
cd d:\powermem\powermem; $env:POWERMEM_SERVER_AUTH_ENABLED="false"; C:\Users\user1\AppData\Local\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.12_qbz5n2kfra8p0\python.exe -m uvicorn src.server.main:app --host 0.0.0.0 --port 8000
```



## 🤝 Contributing

Contributions are welcome! Please see [CONTRIBUTING.md](./CONTRIBUTING.md) for details.

## 📝 Changelog

See [CHANGELOG.md](./CHANGELOG.md) for version history.

## 📄 License

[MIT License](./LICENSE)

## 💬 Support

- **Email**: support@powermem.io
- **Issues**: [GitHub Issues](https://github.com/your-org/powermem-extension/issues)
- **Discord**: [Join Community](https://discord.gg/powermem)

## 🙏 Acknowledgments

Thanks to all contributors and supporters!

---

Made with ❤️ by PowerMem Team
