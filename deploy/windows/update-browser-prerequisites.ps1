param(
  [string]$StartScript = "C:\TaskHub\start-node-agent.ps1",
  [Parameter(Mandatory=$true)][string]$BrowserProfileDir,
  [Parameter(Mandatory=$true)][string]$BrowserAuthTarget
)
$ErrorActionPreference = "Stop"
if (-not (Test-Path $StartScript)) { throw "Node start script not found: $StartScript" }
if (-not (Test-Path $BrowserProfileDir)) { throw "Browser profile not found: $BrowserProfileDir" }
$content = Get-Content $StartScript | Where-Object {
  $_ -notmatch '^\$env:TASKHUB_BROWSER_(PROFILE_DIR|AUTH_TARGET|AUTH_READY_FILE)'
}
$anchor = $content | Select-String -SimpleMatch 'Set-Location' | Select-Object -First 1
if (-not $anchor) { throw "Set-Location anchor not found in node start script" }
$index = $anchor.LineNumber - 1
$settings = @(
  "`$env:TASKHUB_BROWSER_PROFILE_DIR = `"$BrowserProfileDir`"",
  "`$env:TASKHUB_BROWSER_AUTH_TARGET = `"$BrowserAuthTarget`"",
  "`$env:TASKHUB_BROWSER_AUTH_READY_FILE = `"C:\TaskHub\browser-auth-ready.json`""
)
$updated = @($content[0..($index - 1)]) + $settings + @($content[$index..($content.Count - 1)])
Set-Content -Path $StartScript -Value $updated -Encoding ascii
Write-Host "Browser prerequisites configured; authorization readiness was not changed."
