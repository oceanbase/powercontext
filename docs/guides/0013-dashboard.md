# PowerMem Web Dashboard Guide

The PowerMem Web Dashboard provides a visual interface for inspecting, managing, and monitoring your AI agent memories. It ships with the HTTP API server and offers real-time analytics, memory CRUD operations, user profile inspection, and system health monitoring.

---

## Table of Contents

- [Getting Started](#getting-started)
  - [Starting the Server](#starting-the-server)
  - [Accessing the Dashboard](#accessing-the-dashboard)
  - [Docker Deployment](#docker-deployment)
- [Authentication](#authentication)
  - [Configuring API Keys](#configuring-api-keys)
  - [Setting API Key in Dashboard](#setting-api-key-in-dashboard)
- [Dashboard Pages](#dashboard-pages)
  - [Overview Page](#overview-page)
  - [Memories Page](#memories-page)
  - [User Profile Page](#user-profile-page)
  - [Settings Page](#settings-page)
- [Common Workflows](#common-workflows)
  - [Inspect Memories After SDK Integration](#inspect-memories-after-sdk-integration)
  - [Debug Retrieval Quality](#debug-retrieval-quality)
  - [Monitor System Health](#monitor-system-health)
- [Troubleshooting](#troubleshooting)
  - [Dashboard Shows 404](#dashboard-shows-404)
  - [401 Unauthorized Errors](#401-unauthorized-errors)
  - [Stale or Missing Data](#stale-or-missing-data)
  - [Rebuilding Dashboard Assets](#rebuilding-dashboard-assets)
- [Relationship to Other Interfaces](#relationship-to-other-interfaces)

---

## Getting Started

### Starting the Server

The Web Dashboard is served by the PowerMem HTTP API server. To start the server:

> **Note:** If you already have the PowerMem server running, you can skip this step and proceed directly to [Accessing the Dashboard](#accessing-the-dashboard).

```bash
# Using pip-installed server
powermem-server --host 0.0.0.0 --port 8848

# Or using Makefile (development)
make server-dashboard-start
```

The server reads configuration from your `.env` file in the current directory. For initial setup, see the [Getting Started Guide](./0001-getting_started.md).

### Accessing the Dashboard

Once the server is running, open your browser and navigate to:

```
http://localhost:8848/dashboard/
```

> **Note:** The trailing slash is important. Accessing `/dashboard` without the slash will redirect automatically.

You can also access the API documentation (Swagger UI) at:

```
http://localhost:8848/docs
```

### Docker Deployment

If you're using Docker Compose:

```bash
docker-compose -f docker/docker-compose.yml up -d
```

The dashboard is available at the same URL: `http://localhost:8848/dashboard/`

For detailed deployment instructions, see the [Docker & Deployment Guide](https://github.com/oceanbase/powermem/blob/main/docker/README.md).

---

## Authentication

### Configuring API Keys

By default, the PowerMem server runs with authentication **disabled**. To enable authentication:

1. Open your `.env` file
2. Set the following variables:

```bash
# Enable authentication
POWERMEM_SERVER_AUTH_ENABLED=true

# Set one or more API keys (comma-separated)
POWERMEM_SERVER_API_KEYS=your-secret-key-1,your-secret-key-2

# Or use a single key (legacy format)
POWERMEM_SERVER_API_KEY=your-secret-key
```

3. Restart the server (if it's already running; otherwise, just start it):

```bash
powermem-server --host 0.0.0.0 --port 8848
```

> **Security Note:** When authentication is enabled, all API endpoints require a valid API key passed via the `X-API-Key` header or `?api_key=` query parameter.

### Setting API Key in Dashboard

If authentication is enabled, you need to configure the API key in the dashboard:

1. Navigate to the **Settings** page (click the gear icon in the sidebar)
2. Enter your API key in the "API Key" field
3. Click **Save**

The API key is stored in your browser's `localStorage` under the key `powermem_api_key`. It never leaves your browser and is only sent to the PowerMem server with API requests.

> **Privacy Note:** The API key is stored locally in your browser. Clearing browser data will remove it, and you'll need to re-enter it.

---

## Dashboard Pages

The dashboard has four main pages, accessible via the left sidebar navigation:

### Overview Page

**Route:** `/dashboard/`

The Overview page provides real-time analytics and system health monitoring for your memory system.

#### Features

**Stat Cards:**
- **Total Memories** — Total number of stored memory records
- **Avg. Importance** — Average priority score across all memories (0.00–5.00 scale)
- **Access Density** — Average number of hits per memory record
- **Unique Dates** — Number of distinct days with memory activity

**Charts and Visualizations:**

1. **Growth Trend** — Line chart showing daily memory creation volume over time
2. **Memory Categories** — Donut chart showing distribution of memories by classification type
3. **Hot Memories** — Table of most-accessed memories with content snippets and hit counts
4. **Retention Age** — Bar chart showing memory lifecycle distribution (< 1 day, 1-7 days, 7-30 days, > 30 days)
5. **Memory Quality** — Card displaying quality analysis (see below)

**Memory Quality Card:**

Displays memory health metrics including:
- **Low Quality Ratio** — Percentage of memories with quality issues
- **Quality Issues Breakdown** — Horizontal bar chart showing counts for:
  - Missing Metadata
  - Empty Content
  - No Embedding
  - Low Importance

Quality status is color-coded:
- ≤ 10%: Good (green)
- ≤ 20%: Good (blue)
- ≤ 50%: Warning (yellow)
- > 50%: Critical (red)

**System Health Card:**

Shows real-time system status with auto-refresh every 30 seconds:
- **Overall Status** — Operational / Degraded / Down
- **Uptime** — Human-readable duration (e.g., "2d 5h 32m")
- **Configuration** — Storage type and LLM provider
- **Dependencies** — Table of service health with latency metrics

**Filters:**

- **Time Range Selector** — Dropdown to filter data by: Last 7 days, 30 days, 90 days, or All time
- **User ID Filter** — Pass `?user_id=xxx` in URL to scope all data to a specific user
- **Agent ID Filter** — Pass `?agent_id=xxx` in URL to scope all data to a specific agent
- **Refresh Button** — Manually refresh all data
- **Clear Filters** — Appears when filters are active, returns to unfiltered view

### Memories Page

**Route:** `/dashboard/memories`

The Memories page allows you to browse, search, filter, and delete stored memory records.

#### Features

**Filter Panel:**
- **User ID** — Filter memories by user identifier
- **Agent ID** — Filter memories by agent identifier
- **Content Search** — Substring search in memory content (client-side)
- **Apply Filters** — Apply the current filter criteria
- **Clear All** — Reset all filters

**Memory Table:**

Displays memories with the following columns:
- **User ID** — Truncated with tooltip for full value
- **Agent ID** — Truncated with tooltip for full value
- **Content** — First 120 characters, with tooltip showing full text
- **Metadata** — First 2 key-value pairs shown as badges
- **Created At** — Date of creation (hidden on small screens)

**Pagination:**

- 20 records per page
- Prev/Next navigation buttons
- Page number persisted in URL for shareable views

**Row Actions (click the `⋯` menu on any row):**

1. **View Details** — Opens a detail panel showing:
   - Memory ID
   - Full content (whitespace-preserved)
   - Category badge
   - Created At (full datetime)
   - User ID and Agent ID
   - Run ID (from `run_id` field or `metadata.filters.run_id`)
   - Full metadata as formatted JSON

2. **Delete Memory** — Permanently deletes the memory record (requires confirmation)

3. **Copy Raw JSON** — Copies the complete memory JSON to clipboard

> **Note:** The dashboard currently supports **Read** and **Delete** operations only. To create or update memories, use the Python SDK, CLI (`pmem`), or REST API.

### User Profile Page

**Route:** `/dashboard/user-profile`

The User Profile page displays aggregated user-level memory profiles created by PowerMem's intelligent memory system.

#### Features

**Search:**
- **User ID Input** — Enter a user ID to search for specific profiles
- **Fuzzy Search** — The API supports fuzzy matching when a search term is provided
- Press **Enter** or click the search button to execute the search

**Profile Table:**

Displays user profiles with:
- **User ID** — Truncated with tooltip for full value
- **Profile Content** — Truncated at 420px width, with tooltip showing full content
- **Topics** — First 3 topic keys displayed, with "..." indicator if more exist
- **Updated At** — Last update timestamp (full datetime)

**Pagination:**

- 20 profiles per page
- Prev/Next navigation

**View Details:**

Click the **View Details** button on any row to open a detail panel showing:
- User ID
- Full profile content
- Topics as formatted JSON
- Created At and Updated At timestamps

> **Note:** User Profile inspection is **read-only**. Profiles are automatically generated and updated by PowerMem's memory pipeline based on user interactions.

### Settings Page

**Route:** `/dashboard/settings`

The Settings page manages dashboard authentication configuration.

#### Features

**API Key Management:**
- **API Key Input** — Password-masked field for entering your PowerMem server API key
- **Save Button** — Stores the key in browser localStorage
- **Hint Text** — "Required if `auth_enabled` is set to true on the server."

**Privacy Notice:**

A blue information banner explains that the API key is stored only in your browser's local storage and is never transmitted to any server other than your PowerMem instance.

---

## Common Workflows

### Inspect Memories After SDK Integration

After integrating PowerMem into your application using the Python SDK or LangChain:

1. Run your application and perform some operations that create memories
2. Open the dashboard at `http://localhost:8848/dashboard/`
3. Navigate to the **Overview** page to see:
   - Total memory count increasing
   - Growth trend chart updating
   - Memory category distribution
4. Navigate to the **Memories** page to:
   - Browse all stored memories
   - Filter by `user_id` or `agent_id` to inspect specific user/agent data
   - Click **View Details** to examine individual memory content and metadata
   - Verify that intelligent extraction and classification are working correctly

### Debug Retrieval Quality

If your AI agent is not retrieving the expected memories:

1. Go to the **Memories** page
2. Use the **Content Search** filter to find memories related to your query
3. Check the **Memory Quality** card on the Overview page for:
   - High "Low Quality Ratio" (indicates many memories have issues)
   - Missing embeddings (memories cannot be vector-searched without embeddings)
   - Empty content or missing metadata
4. For problematic memories:
   - Click **View Details** to inspect the full JSON structure
   - Verify that `embedding` field is present
   - Check that `metadata` contains expected fields
5. If quality is poor, consider:
   - Re-running memory extraction with better prompts
   - Deleting low-quality memories and re-adding them
   - Checking your embedding provider configuration in `.env`

### Monitor System Health

To ensure your PowerMem server is running smoothly:

1. Open the **Overview** page
2. Check the **System Health Card** (auto-refreshes every 30 seconds):
   - **Overall Status** should show "Operational" (green)
   - **Uptime** shows how long the server has been running
   - **Dependencies** table shows health of LLM, embedding, and storage services
3. If any dependency shows as "Degraded" or "Down":
   - Check the error message in the dependencies table
   - Verify your `.env` configuration for that provider
   - Check network connectivity to external services (OpenAI, Qwen, etc.)
4. Monitor **Memory Quality** over time:
   - A rising "Low Quality Ratio" may indicate issues with memory extraction
   - Regular monitoring helps catch degradation early

---

## Troubleshooting

### Dashboard Shows 404

**Symptom:** Accessing `http://localhost:8848/dashboard/` returns a 404 error.

**Cause:** The dashboard assets have not been built or are not in the correct location.

**Solution:**

```bash
# Build the dashboard assets
make build-dashboard

# Verify the assets exist
ls src/server/dashboard/index.html

# Restart the server (skip if server is not running yet, just start it)
make server-stop
make server-start
```

> **Note:** The server looks for built assets in `src/server/dashboard/`. If this directory doesn't exist, the `/dashboard/` route is not mounted (no error, just 404).

### 401 Unauthorized Errors

**Symptom:** Dashboard shows "401 Unauthorized" or API requests fail with 401.

**Cause:** Authentication is enabled on the server, but no API key is configured in the dashboard.

**Solution:**

1. Go to the **Settings** page in the dashboard
2. Enter your API key (from `.env` file: `POWERMEM_SERVER_API_KEY` or `POWERMEM_SERVER_API_KEYS`)
3. Click **Save**
4. Refresh the page

If you don't have an API key:
1. Check your `.env` file for `POWERMEM_SERVER_API_KEYS` or `POWERMEM_SERVER_API_KEY`
2. If authentication is not needed, set `POWERMEM_SERVER_AUTH_ENABLED=false` and restart the server (or start it if not running)

### Stale or Missing Data

**Symptom:** Dashboard doesn't show recent memories or shows outdated information.

**Possible Causes and Solutions:**

1. **Browser cache:** Click the **Refresh** button on the Overview or Memories page
2. **Wrong user_id/agent_id filter:** Check the URL for `?user_id=xxx` or `?agent_id=xxx` parameters. Click **Clear Filters** if present.
3. **Server not running:** Verify the server is running: `curl http://localhost:8848/api/v1/system/health`
4. **Different database:** Ensure your application and server are using the same `.env` configuration (same database)

### Rebuilding Dashboard Assets

If you've modified the dashboard source code or are experiencing UI issues:

```bash
# Clean rebuild
cd dashboard
rm -rf dist node_modules
pnpm install
pnpm build
cd ..

# Copy to server directory
make build-dashboard

# Restart server (skip if server is not running yet, just start it)
make server-stop
make server-start
```

> **Development Tip:** For active dashboard development, you can run the Vite dev server separately (`cd dashboard && pnpm dev`) and configure it to proxy API requests to your backend. See the [Development Guide](../development/overview.md) for details.

---

## Relationship to Other Interfaces

The Web Dashboard is one of several ways to interact with PowerMem:

| Interface | Purpose | Best For |
|-----------|---------|----------|
| **Web Dashboard** | Visual inspection and monitoring | Browsing memories, analytics, system health |
| **Python SDK** | Programmatic memory operations | Application integration, custom workflows |
| **CLI (`pmem`)** | Command-line memory operations | Quick queries, scripting, backup/migration |
| **REST API** | HTTP-based programmatic access | Custom integrations, non-Python apps |
| **MCP Server** | AI client integration | Claude Code, Cursor, Copilot, etc. |
| **VS Code Extension** | IDE-integrated memory access | Developer workflow, quick notes |

### Web Dashboard vs VS Code Extension Dashboard

The VS Code extension also provides a "Dashboard" button in the status bar. This is **different** from the Web Dashboard:

- **Web Dashboard** (`/dashboard/`): Full-featured web application with analytics, charts, memory management, and system health monitoring
- **VS Code Extension Dashboard**: Opens the same web dashboard in an embedded browser panel within VS Code, providing quick access without leaving the IDE

Both interfaces connect to the same PowerMem server and show the same data.

### When to Use the Dashboard

Use the Web Dashboard when you need to:
- Visually explore and browse memories
- Monitor system health and memory quality metrics
- Analyze memory growth trends and usage patterns
- Inspect user profiles and memory metadata
- Troubleshoot retrieval or quality issues

Use the SDK, CLI, or API when you need to:
- Create or update memories programmatically
- Integrate memory operations into your application
- Perform bulk operations or migrations
- Automate memory management tasks

---

## Additional Resources

- [API Server Documentation](../api/0005-api_server.md) — Server configuration and deployment
- [Getting Started Guide](./0001-getting_started.md) — Initial setup and SDK usage
- [Configuration Guide](./0003-configuration.md) — Environment variables and settings
- [Docker & Deployment](https://github.com/oceanbase/powermem/blob/main/docker/README.md) — Production deployment
- [Development Guide](../development/overview.md) — Building and customizing the dashboard
