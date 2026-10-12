# uninstall_dev.ps1 — Instantly remove StyleCut Panel
# ====================================================
$PanelDest = Join-Path "$env:APPDATA\Adobe\CEP\extensions" "com.stylecut.panel"

if (Test-Path $PanelDest) {
    # If it was a junction
    cmd /c "rmdir `"$PanelDest`"" 2>$null | Out-Null
    # If it is a real folder
    if (Test-Path $PanelDest) {
        Remove-Item -Path $PanelDest -Recurse -Force
    }
    Write-Host "✅ StyleCut panel completely removed from $PanelDest"
} else {
    Write-Host "StyleCut panel was not present in extensions folder."
}
