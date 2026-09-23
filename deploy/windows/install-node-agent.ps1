param(
  [Parameter(Mandatory=$true)][string]$Python,
  [Parameter(Mandatory=$true)][string]$PackagePath,
  [string]$TaskName = "TaskHubCandidateNodeAgent",
  [string]$NodeId = "windows-gui-34-candidate",
  [int]$Port = 8391,
  [string]$WorkRoot = "",
  [string]$BrowserProfileDir = "C:\TaskHub\profiles\acceptance",
  [Parameter(Mandatory=$true)][string]$BrowserAuthTarget
)
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Security
$Root = "C:\TaskHub"
$Venv = Join-Path $Root "venv"
$Jobs = $WorkRoot
if ([string]::IsNullOrWhiteSpace($Jobs)) {
  $DataDrive = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='D:'" `
    -ErrorAction SilentlyContinue
  if ($DataDrive -and $DataDrive.DriveType -eq 3 -and $DataDrive.FileSystem `
      -and $DataDrive.FreeSpace -gt 1GB) {
    $Jobs = "D:\TaskHub\jobs"
  } else {
    $Jobs = Join-Path $Root "jobs"
  }
}
$Secrets = Join-Path $Root "secrets"
$CacheRoot = Join-Path (Split-Path -Parent $Jobs) "cache"
$TokenFile = Join-Path $Secrets "node-token.dpapi"
$StartScript = Join-Path $Root "start-node-agent.ps1"
$Identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name

if (-not $env:TASKHUB_NODE_TOKEN) {
  throw "Set TASKHUB_NODE_TOKEN in this PowerShell session before installing"
}
if (-not (Test-Path $PackagePath)) {
  throw "Candidate package not found: $PackagePath"
}

New-Item -ItemType Directory -Force -Path $Jobs, $Secrets, $CacheRoot | Out-Null
New-Item -ItemType Directory -Force -Path $BrowserProfileDir | Out-Null
& icacls.exe $Jobs /inheritance:r /grant:r "${Identity}:(OI)(CI)M" | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Failed to grant the node account workspace access" }
& $Python -m venv $Venv
if ($LASTEXITCODE -ne 0) { throw "Python virtual environment creation failed" }
$VenvPython = Join-Path $Venv "Scripts\python.exe"
& $VenvPython -m pip install --timeout 180 --retries 10 $PackagePath `
  "pytest>=8.3,<10" "playwright>=1.51,<2"
if ($LASTEXITCODE -ne 0) { throw "Python dependency installation failed" }
& $VenvPython -m pip install --no-deps --force-reinstall $PackagePath
if ($LASTEXITCODE -ne 0) { throw "Candidate package installation failed" }
$Probe = & $VenvPython -m taskhub_v2.node_agent.browser_probe | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or -not $Probe.capabilities.windows_gui `
    -or -not $Probe.capabilities.chromium -or -not $Probe.capabilities.edge) {
  throw "System Chrome and Edge must both pass the interactive Playwright probe"
}

# Machine-scoped DPAPI is stable across SSH and interactive logon sessions. The
# file ACL still limits access to the account that runs the browser task.
$TokenBytes = [System.Text.Encoding]::UTF8.GetBytes($env:TASKHUB_NODE_TOKEN)
$EncryptedToken = [System.Security.Cryptography.ProtectedData]::Protect(
  $TokenBytes,
  $null,
  [System.Security.Cryptography.DataProtectionScope]::LocalMachine
)
[System.IO.File]::WriteAllBytes($TokenFile, $EncryptedToken)
& icacls.exe $Secrets /inheritance:r /grant:r "${Identity}:(OI)(CI)F" | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Failed to protect the node token directory" }

$Launch = @"
`$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Security
`$EncryptedToken = [System.IO.File]::ReadAllBytes("$TokenFile")
`$TokenBytes = [System.Security.Cryptography.ProtectedData]::Unprotect(
  `$EncryptedToken,
  `$null,
  [System.Security.Cryptography.DataProtectionScope]::LocalMachine
)
`$env:TASKHUB_NODE_TOKEN = [System.Text.Encoding]::UTF8.GetString(`$TokenBytes)
`$env:TASKHUB_NODE_ID = "$NodeId"
`$env:TASKHUB_NODE_WORK_ROOT = "$Jobs"
`$env:TASKHUB_NODE_CACHE_ROOT = "$CacheRoot"
`$env:TASKHUB_WINDOWS_GUI = "true"
`$env:TASKHUB_BROWSER_PROFILE_DIR = "$BrowserProfileDir"
`$env:TASKHUB_BROWSER_AUTH_TARGET = "$BrowserAuthTarget"
`$env:TASKHUB_BROWSER_AUTH_READY_FILE = "$Root\browser-auth-ready.json"
Set-Location "$Root"
& "$VenvPython" -m uvicorn taskhub_v2.node_agent:create_node_app --factory --host 0.0.0.0 --port $Port
"@
Set-Content -Path $StartScript -Value $Launch -Encoding ascii

$Action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument (
  "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$StartScript`""
)
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $Identity
$Principal = New-ScheduledTaskPrincipal -UserId $Identity -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger `
  -Principal $Principal -Description "TaskHub interactive Windows browser node" -Force | Out-Null
$FirewallName = "TaskHub-$NodeId-$Port"
$Firewall = Get-NetFirewallRule -DisplayName $FirewallName -ErrorAction SilentlyContinue
if ($Firewall) {
  Set-NetFirewallRule -DisplayName $FirewallName -Enabled True -Profile Any
} else {
  New-NetFirewallRule -DisplayName $FirewallName -Direction Inbound -Action Allow `
    -Protocol TCP -LocalPort $Port -Profile Any | Out-Null
}
Start-ScheduledTask -TaskName $TaskName

Write-Host "TaskHub candidate node scheduled for $Identity on port $Port; work root: $Jobs"
