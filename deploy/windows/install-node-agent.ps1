param(
  [Parameter(Mandatory=$true)][string]$Python,
  [string]$ServiceName = "TaskHubNodeAgent"
)
$ErrorActionPreference = "Stop"
$Root = if (Test-Path "D:\") { "D:\TaskHub" } else { "C:\TaskHub" }
$Venv = Join-Path $Root "venv"
$Jobs = Join-Path $Root "jobs"
New-Item -ItemType Directory -Force -Path $Jobs | Out-Null
& $Python -m venv $Venv
& (Join-Path $Venv "Scripts\python.exe") -m pip install taskhub-v2

# The token is intentionally not accepted on the command line. Configure
# TASKHUB_NODE_TOKEN using the existing protected service environment mechanism.
$Exe = Join-Path $Venv "Scripts\python.exe"
$Args = "-m uvicorn taskhub_v2.node_agent:create_node_app --factory --host 192.168.31.34 --port 8301"
if (-not (Get-Command nssm.exe -ErrorAction SilentlyContinue)) {
  throw "nssm.exe is required to register the Windows service"
}
& nssm.exe install $ServiceName $Exe $Args
& nssm.exe set $ServiceName AppDirectory $Root
& nssm.exe set $ServiceName AppEnvironmentExtra "TASKHUB_NODE_ID=windows-gui-34" "TASKHUB_NODE_WORK_ROOT=$Jobs"
& nssm.exe set $ServiceName Start SERVICE_AUTO_START
& nssm.exe start $ServiceName
