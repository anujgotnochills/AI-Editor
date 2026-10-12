# deploy_local.ps1 — Deploy StyleCut Panel cleanly as a physical folder
# ======================================================================
# Copies files directly into Adobe's extension folder (NO cross-drive junctions).
# Exactly like commercial installers (Captik, AutoCut, etc.).

$ErrorActionPreference = "Stop"

$SrcDir  = Join-Path $PSScriptRoot "com.stylecut.panel"
$DestDir = Join-Path "$env:APPDATA\Adobe\CEP\extensions" "com.stylecut.panel"

Write-Host "Deploying StyleCut panel..."
Write-Host "Source: $SrcDir"
Write-Host "Target: $DestDir"

# Ensure destination directory exists and is clean
if (Test-Path $DestDir) {
    Remove-Item -Path $DestDir -Recurse -Force
}

# Copy as a REAL physical directory (not a junction)
Copy-Item -Path $SrcDir -Destination $DestDir -Recurse -Force

# Verify
if (Test-Path "$DestDir\CSXS\manifest.xml") {
    Write-Host ""
    Write-Host "✅ StyleCut deployed successfully as a physical directory."
    Write-Host "   Path: $DestDir"
    Write-Host ""
    Write-Host "To test:"
    Write-Host "1. In one terminal, start engine: python engine.py"
    Write-Host "2. Restart Premiere Pro"
    Write-Host "3. Window > Extensions > StyleCut"
} else {
    Write-Error "Deployment failed: manifest.xml not found in destination."
}
