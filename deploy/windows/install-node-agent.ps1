param(
  [Parameter(Mandatory=$true)][string]$Python,
  [Parameter(Mandatory=$true)][string]$PackagePath,
  [string]$TaskName = "TaskHubCandidateNodeAgent",
  [string]$NodeId = "windows-gui-34-candidate",
  [int]$Port = 8391
)
$ErrorActionPreference = "Stop"
$Root = "C:\TaskHub"
$Venv = Join-Path $Root "venv"
$Jobs = Join-Path $Root "jobs"
$Secrets = Join-Path $Root "secrets"
$TokenFile = Join-Path $Secrets "node-token.dpapi"
$StartScript = Join-Path $Root "start-node-agent.ps1"

if (-not $env:TASKHUB_NODE_TOKEN) {
  throw "Set TASKHUB_NODE_TOKEN in this PowerShell session before installing"
}
if (-not (Test-Path $PackagePath)) {
  throw "Candidate package not found: $PackagePath"
}

New-Item -ItemType Directory -Force -Path $Jobs, $Secrets | Out-Null
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

# DPAPI binds the token to the interactive Windows account that runs the task.
$SecureToken = ConvertTo-SecureString $env:TASKHUB_NODE_TOKEN -AsPlainText -Force
$SecureToken | ConvertFrom-SecureString | Set-Content -Encoding ascii $TokenFile
$Identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
& icacls.exe $Secrets /inheritance:r /grant:r "${Identity}:(OI)(CI)F" | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Failed to protect the node token directory" }

$Launch = @"
`$ErrorActionPreference = "Stop"
`$SecureToken = Get-Content "$TokenFile" | ConvertTo-SecureString
`$Credential = New-Object System.Management.Automation.PSCredential("taskhub", `$SecureToken)
`$env:TASKHUB_NODE_TOKEN = `$Credential.GetNetworkCredential().Password
`$env:TASKHUB_NODE_ID = "$NodeId"
`$env:TASKHUB_NODE_WORK_ROOT = "$Jobs"
`$env:TASKHUB_WINDOWS_GUI = "true"
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

Write-Host "TaskHub candidate node scheduled for $Identity on port $Port"
