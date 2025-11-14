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
cd docs/website
rm -rf .docusaurus node_modules build || true
echo "✓ Cache cleaned"
echo ""

# Step 2: Sync documentation files
echo "Step 2: Syncing documentation files..."
cd "$PROJECT_ROOT"

# Delete all files in docs/website/docs directory
echo "  Cleaning docs/website/docs directory..."
rm -rf docs/website/docs/*
find docs/website/docs -mindepth 1 -maxdepth 1 ! -name '.git' -exec rm -rf {} + 2>/dev/null || true

# Copy all files from docs/ to docs/website/docs/ (excluding website and images folders)
echo "  Copying documentation files from docs/ to docs/website/docs/..."
cd docs
# List all directories and copy them (excluding website and images folders)
for dirname in $(ls -d */ 2>/dev/null | grep -vE "^(website|images)/$"); do
    dirname="${dirname%/}"  # Remove trailing slash
    echo "dirname: $dirname"
    if [ -d "$dirname" ]; then
        echo "    Copying directory: $dirname"
        cp -r "$dirname" website/docs/
    fi
done
rm -rf website/docs/*.md
mkdir -p website/docs/api/
cp -r api/* website/docs/api/


cd "$PROJECT_ROOT"

# List copied files for verification
echo ""
echo "  Copied documentation files:"
ls -la docs/website/docs/
echo ""
echo "  Verifying copied directories:"
find docs/website/docs -type d -maxdepth 1 | sort
echo "✓ Documentation files synced"
echo "  Note: File renaming will be handled by docusaurus-plugin-rename-docs during build"
echo ""

# Step 3: Install dependencies
echo "Step 3: Installing dependencies..."
cd docs/website
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

