param(
  [string]$Browser = "chrome",
  [string]$ProfileDir = "C:\TaskHub\profiles\acceptance",
  [Parameter(Mandatory=$true)][string]$AuthTarget
)
$ErrorActionPreference = "Stop"
$executables = @{
  chrome = "C:\Program Files\Google\Chrome\Application\chrome.exe"
  edge = "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
}
$executable = $executables[$Browser]
if (-not $executable -or -not (Test-Path $executable)) { throw "Browser not found: $Browser" }
New-Item -ItemType Directory -Force -Path $ProfileDir | Out-Null
$process = Start-Process -FilePath $executable -ArgumentList @(
  "--user-data-dir=$ProfileDir", "--profile-directory=Default", $AuthTarget
) -PassThru
Write-Host "Complete login and authorized-target verification, close the browser, then press Enter."
[void](Read-Host)
if (-not $process.HasExited) { throw "Close the browser before marking authorization ready" }
@{ target = $AuthTarget; verified_at = (Get-Date).ToUniversalTime().ToString("o") } |
  ConvertTo-Json | Set-Content -Path "C:\TaskHub\browser-auth-ready.json" -Encoding ascii
Write-Host "Browser profile authorization marked ready. Restart the TaskHub node agent."
