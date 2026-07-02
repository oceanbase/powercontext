#!/bin/bash
# Frontend deployment script for PowerMem documentation
# This script follows the same steps as the GitHub Actions workflow

set -e

echo "=========================================="
echo "PowerMem Frontend Deployment Script"
echo "=========================================="
echo ""

# Check if Node.js is installed
if ! command -v node &> /dev/null; then
    echo "Error: Node.js is not installed"
    echo "Please install Node.js 20 or higher"
    exit 1
fi

# Check Node.js version
NODE_VERSION=$(node -v | cut -d'v' -f2 | cut -d'.' -f1)
if [ "$NODE_VERSION" -lt 20 ]; then
    echo "Error: Node.js version must be 20 or higher (current: $(node -v))"
    exit 1
fi

echo "✓ Node.js version: $(node -v)"
echo ""

# Navigate to project root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

echo "Project root: $PROJECT_ROOT"
echo ""

# Step 1: Clean cache
echo "Step 1: Cleaning cache..."
cd "$PROJECT_ROOT/docs/website"
rm -rf .docusaurus node_modules build || true
echo "✓ Cache cleaned"
echo ""

# Step 2: Sync documentation files
echo "Step 2: Syncing documentation files..."
"$PROJECT_ROOT/scripts/sync-website-docs.sh"

# List copied files for verification
echo ""
echo "  Copied documentation files:"
ls -la "$PROJECT_ROOT/docs/website/docs/"
echo ""
echo "  Verifying copied directories:"
find "$PROJECT_ROOT/docs/website/docs" -maxdepth 1 -type d | sort
echo "✓ Documentation files synced"
echo "  Note: File renaming will be handled by docusaurus-plugin-rename-docs during build"
echo ""

# Step 3: Install dependencies
echo "Step 3: Installing dependencies..."
cd "$PROJECT_ROOT/docs/website"
if [ ! -f "package-lock.json" ]; then
    echo "  Warning: package-lock.json not found, running npm install..."
    npm install
else
    npm ci --prefer-offline=false
fi
echo "✓ Dependencies installed"
echo ""

# Step 4: Build
echo "Step 4: Building documentation site..."
npm run build
echo "✓ Build completed"
echo ""

# Step 5: Display results
echo "=========================================="
echo "Build Summary"
echo "=========================================="
echo "Build output directory: docs/website/build"
echo ""
echo "To preview the site locally, run:"
echo "  cd docs/website && npm run serve"
echo ""
echo "Or use:"
echo "  cd docs/website && npx serve build"
echo ""
echo "To deploy to GitHub Pages:"
echo "  1. Push changes to the main branch"
echo "  2. GitHub Actions will automatically deploy"
echo "  Or manually trigger the workflow from GitHub Actions"
echo "=========================================="
