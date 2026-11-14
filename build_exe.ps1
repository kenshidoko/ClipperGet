param(
    [string]$PfxPath = $env:CLIPGETTER_PFX_PATH,
    [string]$PfxPassword = $env:CLIPGETTER_PFX_PASSWORD,
    [string]$CertThumbprint = $env:CLIPGETTER_CERT_THUMBPRINT,
    [switch]$KeepBuildCache
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if ($PfxPath) { $env:CLIPGETTER_PFX_PATH = $PfxPath }
if ($PfxPassword) { $env:CLIPGETTER_PFX_PASSWORD = $PfxPassword }
if ($CertThumbprint) { $env:CLIPGETTER_CERT_THUMBPRINT = $CertThumbprint }

Write-Host "Building ClipGetter portable EXE + app bundle with PyInstaller..." -ForegroundColor Cyan
pyinstaller --clean --noconfirm ClipGetter.spec

$SignScript = Join-Path $PSScriptRoot "installer\sign_release.ps1"

if (Test-Path ".\dist\ClipGetter.exe") {
    Move-Item ".\dist\ClipGetter.exe" ".\ClipGetter.exe" -Force
    if (Test-Path $SignScript) {
        & $SignScript -TargetFiles @((Join-Path $PSScriptRoot "ClipGetter.exe"))
    }
    $sizeMB = [math]::Round((Get-Item ".\ClipGetter.exe").Length / 1MB, 1)
    Write-Host "SUCCESS: Built ClipGetter.exe ($sizeMB MB)" -ForegroundColor Green
} else {
    Write-Error "Build failed: dist\ClipGetter.exe not found."
}

$InstallerScript = Join-Path $PSScriptRoot "installer\build_installer.ps1"
if (Test-Path $InstallerScript) {
    & powershell -ExecutionPolicy Bypass -File $InstallerScript
}

if (-not $KeepBuildCache) {
    Write-Host "Cleaning temporary build/ and dist/ staging folders to save disk space..." -ForegroundColor Cyan
    if (Test-Path ".\build") { Remove-Item ".\build" -Recurse -Force -ErrorAction SilentlyContinue }
    if (Test-Path ".\dist") { Remove-Item ".\dist" -Recurse -Force -ErrorAction SilentlyContinue }
}
