param([string]$EnvFile = "")
$ErrorActionPreference = "Continue"
$PSDefaultParameterValues["*:ErrorAction"] = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $EnvFile) { $EnvFile = Join-Path $Root ".env" }
$Failures = 0
$Warnings = 0
function Pass([string]$Message) { Write-Host "[通过] $Message" }
function Warn([string]$Message) { $script:Warnings++; Write-Warning $Message }
function Fail([string]$Message) { $script:Failures++; Write-Host "[失败] $Message" -ForegroundColor Red }
function Get-EnvValue([string]$Name) {
    if (-not (Test-Path $EnvFile)) { return "" }
    $Line = Get-Content $EnvFile | Where-Object { $_ -match "^$([regex]::Escape($Name))=" } | Select-Object -Last 1
    if ($Line) { return ($Line -split '=', 2)[1] }
    return ""
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { Fail "缺少 docker 命令" }
else {
    docker info *> $null
    if ($LASTEXITCODE -eq 0) { Pass "Docker Engine 可访问" } else { Fail "Docker Desktop 未启动" }
    docker compose version *> $null
    if ($LASTEXITCODE -eq 0) { Pass "Docker Compose v2 可用" } else { Fail "缺少 Docker Compose v2" }
    $Arch = docker version --format '{{.Server.Arch}}' 2>$null
    if ($Arch -in @("amd64", "x86_64")) { Pass "Docker 架构为 amd64" } else { Fail "当前公开镜像不支持 Docker 架构: $Arch" }
}

$Computer = Get-CimInstance Win32_ComputerSystem
if ($Computer.NumberOfLogicalProcessors -ge 4) { Pass "CPU: $($Computer.NumberOfLogicalProcessors) 逻辑核" } else { Warn "CPU 低于建议的 4 核" }
if ($Computer.TotalPhysicalMemory -ge 8GB) { Pass "内存不少于 8 GB" } else { Warn "内存低于建议的 8 GB" }
$DriveName = [IO.Path]::GetPathRoot($Root).Substring(0, 1)
$Drive = Get-PSDrive -Name $DriveName
if ($Drive.Free -ge 40GB) { Pass "可用磁盘不少于 40 GB" } else { Warn "可用磁盘低于建议的 40 GB" }

if (-not (Test-Path $EnvFile)) {
    Warn "尚未创建 $EnvFile；将按 .env.example 检查"
    $EnvFile = Join-Path $Root ".env.example"
}
if (-not (Test-Path $EnvFile)) { Fail "缺少 .env 和 .env.example" }
$Port = Get-EnvValue "TASKHUB_PORT"
if (-not $Port) { $Port = "8200" }
$Listener = Get-NetTCPConnection -State Listen -LocalPort ([int]$Port) -ErrorAction SilentlyContinue
if ($Listener) {
    $Existing = docker ps --format '{{.Names}}' | Where-Object { $_ -eq "taskhub-controller-1" }
    if ($Existing) { Pass "端口 $Port 已由现有 TaskHub 使用" } else { Fail "端口 $Port 已被其他进程占用" }
} else { Pass "端口 $Port 未发现冲突" }

foreach ($Key in @("TASKHUB_SEED_IMAGE", "TASKHUB_NODE_IMAGE", "TASKHUB_POSTGRES_IMAGE", "TASKHUB_DOCKER_PROXY_IMAGE")) {
    $Image = Get-EnvValue $Key
    if (-not $Image) {
        $Defaults = @{
            TASKHUB_SEED_IMAGE = "ghcr.io/gryps/taskhub-seed:0.1.0-alpha"
            TASKHUB_NODE_IMAGE = "ghcr.io/gryps/taskhub-node:0.1.0-alpha"
            TASKHUB_POSTGRES_IMAGE = "postgres:16-alpine"
            TASKHUB_DOCKER_PROXY_IMAGE = "ghcr.io/tecnativa/docker-socket-proxy:v0.5.0"
        }
        $Image = $Defaults[$Key]
        Warn "$Key 未显式配置，将使用 Compose 默认值 $Image"
    }
    docker image inspect $Image *> $null
    if ($LASTEXITCODE -ne 0) { docker manifest inspect $Image *> $null }
    if ($LASTEXITCODE -eq 0) { Pass "镜像可用: $Image" } else { Fail "无法读取镜像: $Image" }
}
if ($Failures -gt 0) { throw "预检失败: $Failures 项失败，$Warnings 项警告。" }
Write-Host "预检通过: $Warnings 项警告。可以运行 .\init.ps1。"
