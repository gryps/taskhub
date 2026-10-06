param(
  [Parameter(Mandatory=$true)][string]$Python,
  [Parameter(Mandatory=$true)][string]$PackagePath,
  [Parameter(Mandatory=$true)][string]$NodeId,
  [int]$Port = 8301,
  [int]$Slots = 1,
  [bool]$BrowserMode = $false,
  [string]$BrowserAuthTarget = ""
)
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Security
$Root = "C:\TaskHub"
$Venv = Join-Path $Root "venv"
$Jobs = Join-Path $Root "jobs"
$Cache = Join-Path $Root "cache\pip"
$DependencyMarker = Join-Path $Venv ".taskhub-dependencies-v1"
$Secrets = Join-Path $Root "secrets"
$TokenFile = Join-Path $Secrets "node-token.dpapi"
$StartScript = Join-Path $Root "start-node-agent.ps1"
$TaskName = "TaskHubNodeAgent"
$Identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
if (-not $env:TASKHUB_NODE_TOKEN) { throw "Missing one-time node credential" }
New-Item -ItemType Directory -Force -Path $Root,$Jobs,$Cache,$Secrets | Out-Null
if (-not (Test-Path $Venv)) { & $Python -m venv $Venv }
$VenvPython = Join-Path $Venv "Scripts\python.exe"
$Packages = @($PackagePath,"pytest>=8.3,<10")
if ($BrowserMode) { $Packages += "playwright>=1.51,<2" }
if (-not (Test-Path $DependencyMarker)) {
  & $VenvPython -m pip install --cache-dir $Cache --timeout 180 --retries 5 @Packages
  if ($LASTEXITCODE -ne 0) { throw "Agent dependencies could not be installed" }
  Set-Content -Path $DependencyMarker -Value "ready" -Encoding ascii
}
& $VenvPython -m pip install --no-deps --force-reinstall $PackagePath
if ($LASTEXITCODE -ne 0) { throw "TaskHub Agent package could not be installed" }
$Bytes = [Text.Encoding]::UTF8.GetBytes($env:TASKHUB_NODE_TOKEN)
$Encrypted = [Security.Cryptography.ProtectedData]::Protect(
  $Bytes,$null,[Security.Cryptography.DataProtectionScope]::LocalMachine)
[IO.File]::WriteAllBytes($TokenFile,$Encrypted)
& icacls.exe $Secrets /inheritance:r /grant:r "${Identity}:(OI)(CI)F" | Out-Null
$BrowserLines = ""
if ($BrowserMode) {
  $Profile = Join-Path $Root "profiles\acceptance"
  New-Item -ItemType Directory -Force -Path $Profile | Out-Null
  $BrowserLines = @"
`$env:TASKHUB_WINDOWS_GUI = "true"
`$env:TASKHUB_BROWSER_PROFILE_DIR = "$Profile"
`$env:TASKHUB_BROWSER_AUTH_TARGET = "$BrowserAuthTarget"
`$env:TASKHUB_BROWSER_AUTH_READY_FILE = "$Root\browser-auth-ready.json"
"@
}
$Launch = @"
`$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Security
`$Encrypted = [IO.File]::ReadAllBytes("$TokenFile")
`$Bytes = [Security.Cryptography.ProtectedData]::Unprotect(`$Encrypted,`$null,[Security.Cryptography.DataProtectionScope]::LocalMachine)
`$env:TASKHUB_NODE_TOKEN = [Text.Encoding]::UTF8.GetString(`$Bytes)
`$env:TASKHUB_NODE_ID = "$NodeId"
`$env:TASKHUB_NODE_ROLE = "test"
`$env:TASKHUB_NODE_SLOTS = "$Slots"
`$env:TASKHUB_NODE_WORK_ROOT = "$Jobs"
$BrowserLines
Set-Location "$Root"
& "$VenvPython" -m uvicorn taskhub_v2.node_agent:create_node_app --factory --host 0.0.0.0 --port $Port
"@
Set-Content -Path $StartScript -Value $Launch -Encoding utf8
$Action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$StartScript`""
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $Identity
$Principal = New-ScheduledTaskPrincipal -UserId $Identity -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Principal $Principal -Description "TaskHub native Windows test node" -Force | Out-Null
$FirewallName = "TaskHub-$NodeId-$Port"
if (Get-NetFirewallRule -DisplayName $FirewallName -ErrorAction SilentlyContinue) {
  Set-NetFirewallRule -DisplayName $FirewallName -Enabled True -Profile Any
} else {
  New-NetFirewallRule -DisplayName $FirewallName -Direction Inbound -Action Allow -Protocol TCP -LocalPort $Port -Profile Any | Out-Null
}
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*taskhub_v2.node_agent*--port $Port*" } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 2
if (-not (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)) {
  Start-Process -FilePath "powershell.exe" -WindowStyle Hidden -ArgumentList @(
    "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$StartScript`"")
}
Write-Host "TaskHub Windows Agent installed: $NodeId"
