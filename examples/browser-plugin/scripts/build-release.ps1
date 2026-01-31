# PowerShell 发布构建脚本
# Windows环境下使用

Write-Host "🚀 Building PowerMem Extension for Release..." -ForegroundColor Cyan

# 1. 清理旧构建
Write-Host "`n📦 Cleaning old build..." -ForegroundColor Yellow
if (Test-Path "dist") {
    Remove-Item -Recurse -Force dist
}

# 2. 安装依赖
Write-Host "`n📥 Installing dependencies..." -ForegroundColor Yellow
npm ci
if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ Failed to install dependencies" -ForegroundColor Red
    exit 1
}

# 3. 运行测试
Write-Host "`n🧪 Running tests..." -ForegroundColor Yellow
npm run test -- --run
if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ Tests failed" -ForegroundColor Red
    exit 1
}

# 4. Lint 检查
Write-Host "`n🔍 Running linter..." -ForegroundColor Yellow
npm run lint
if ($LASTEXITCODE -ne 0) {
    Write-Host "⚠️  Linter found issues, but continuing..." -ForegroundColor Yellow
}

# 5. 构建
Write-Host "`n🏗️  Building extension..." -ForegroundColor Yellow
$env:NODE_ENV = "production"
npm run build
if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ Build failed" -ForegroundColor Red
    exit 1
}

# 6. 优化图标（如果有）
Write-Host "`n🖼️  Optimizing images..." -ForegroundColor Yellow
if (Test-Path "dist/icons") {
    Write-Host "Icons found in dist/icons" -ForegroundColor Green
} else {
    Write-Host "No icons to optimize" -ForegroundColor Yellow
}

# 7. 获取版本号
$packageJson = Get-Content "package.json" | ConvertFrom-Json
$version = $packageJson.version

# 8. 生成 ZIP
Write-Host "`n📦 Creating release package..." -ForegroundColor Yellow
$zipName = "powermem-extension-v$version.zip"

if (Test-Path $zipName) {
    Remove-Item $zipName
}

# 压缩dist目录
Compress-Archive -Path "dist\*" -DestinationPath $zipName

Write-Host "`n✅ Build complete!" -ForegroundColor Green
Write-Host "📦 Package: $zipName" -ForegroundColor Cyan
Write-Host "📊 Size: $([math]::Round((Get-Item $zipName).Length / 1MB, 2)) MB" -ForegroundColor Cyan

# 9. 显示检查清单
Write-Host "`n📋 Pre-Release Checklist:" -ForegroundColor Yellow
Write-Host "  [ ] All tests passed"
Write-Host "  [ ] Lint checks completed"
Write-Host "  [ ] Package size < 2MB"
Write-Host "  [ ] README updated"
Write-Host "  [ ] Version number bumped"
Write-Host "  [ ] Icons prepared (16x16, 48x48, 128x128)"
Write-Host "  [ ] Screenshots ready for Chrome Web Store"
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "  1. Test the extension manually"
Write-Host "  2. Update CHANGELOG.md"
Write-Host "  3. Create a git tag: git tag v$version"
Write-Host "  4. Upload to Chrome Web Store"
Write-Host ""
