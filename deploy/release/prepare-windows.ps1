param([switch]$InstallDockerDesktop)
$ErrorActionPreference = "Continue"
$PSDefaultParameterValues["*:ErrorAction"] = "Stop"

$Os = Get-CimInstance Win32_OperatingSystem
$Computer = Get-CimInstance Win32_ComputerSystem
if (-not $Os.Caption.Contains("Windows 10") -and -not $Os.Caption.Contains("Windows 11")) {
    throw "仅验收 Windows 10/11 Docker Desktop，当前是 $($Os.Caption)。"
}
if ($env:PROCESSOR_ARCHITECTURE -notin @("AMD64", "x86_64")) {
    throw "当前公开 TaskHub 镜像仅支持 amd64，主机架构是 $env:PROCESSOR_ARCHITECTURE。"
}
if ($Computer.TotalPhysicalMemory -lt 8GB) { Write-Warning "内存低于建议的 8 GB。" }

wsl.exe --status *> $null
if ($LASTEXITCODE -ne 0) {
    throw "WSL 2 未就绪。请先在管理员 PowerShell 执行 wsl --install --no-distribution，重启后再运行本脚本。"
}

$Docker = Get-Command docker -ErrorAction SilentlyContinue
if (-not $Docker) {
    if (-not $InstallDockerDesktop) {
        throw "Docker Desktop 未安装。确认安装后运行: .\prepare-windows.ps1 -InstallDockerDesktop"
    }
    $Winget = Get-Command winget -ErrorAction SilentlyContinue
    if (-not $Winget) { throw "缺少 winget，无法自动安装 Docker Desktop。" }
    winget install --id Docker.DockerDesktop --exact --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) { throw "Docker Desktop 安装失败。" }
    Write-Host "Docker Desktop 已安装。完成首次启动和许可确认后，重新运行本脚本。"
    exit 0
}

docker info *> $null
if ($LASTEXITCODE -ne 0) {
    $Desktop = Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"
    if (Test-Path $Desktop) { Start-Process $Desktop }
    throw "Docker Desktop 尚未就绪；已尝试启动，请等待后重新运行。"
}
docker compose version *> $null
if ($LASTEXITCODE -ne 0) { throw "缺少 Docker Compose v2。" }
Write-Host "Windows 宿主机准备完成。下一步: .\preflight.ps1; .\init.ps1"
