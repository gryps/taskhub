$ErrorActionPreference = "Continue"
$PSDefaultParameterValues["*:ErrorAction"] = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$EnvFile = Join-Path $Root ".env"
if (-not (Test-Path $EnvFile)) { throw "缺少 $EnvFile。" }
function Get-EnvValue([string]$Name) {
    $Line = Get-Content $EnvFile | Where-Object { $_ -match "^$([regex]::Escape($Name))=" } | Select-Object -Last 1
    if ($Line) { return ($Line -split '=', 2)[1] }
    return ""
}
foreach ($Name in @("taskhub-controller-1", "taskhub-postgres-1", "taskhub-docker-proxy-1")) {
    $Inspection = docker inspect $Name 2>$null | ConvertFrom-Json
    if (-not $Inspection -or $Inspection[0].State.Status -ne "running") { throw "容器未运行: $Name" }
    Write-Host "[通过] $Name 正在运行"
}
$Controller = (docker inspect taskhub-controller-1 | ConvertFrom-Json)[0]
if ($Controller.State.Health.Status -ne "healthy") { throw "Seed 健康状态异常。" }
$Port = Get-EnvValue "TASKHUB_PORT"; if (-not $Port) { $Port = "8200" }
$Scheme = if ((Get-EnvValue "TASKHUB_ENFORCE_HTTPS") -eq "true") { "https" } else { "http" }
if ($Scheme -eq "https") { [Net.ServicePointManager]::ServerCertificateValidationCallback = { $true } }
$Health = Invoke-RestMethod "${Scheme}://127.0.0.1:$Port/api/health" -TimeoutSec 10
if ($Health.status -ne "ok") { throw "API 健康响应异常。" }
$Page = Invoke-WebRequest -UseBasicParsing "${Scheme}://127.0.0.1:$Port/" -TimeoutSec 10
if (-not $Page.Content.Contains("/static/app.js")) { throw "前端入口缺少 app.js。" }
Write-Host "[通过] Web/API: ${Scheme}://127.0.0.1:$Port"

$NodeImage = Get-EnvValue "TASKHUB_NODE_IMAGE"
$ExpectedNode = (docker image inspect $NodeImage | ConvertFrom-Json)[0]
$ExpectedRevision = $ExpectedNode.Config.Labels.'org.opencontainers.image.revision'
$Nodes = @(docker ps -a --filter label=io.taskhub.managed=true --format '{{.Names}}')
if ($Nodes.Count -eq 0) { Write-Warning "尚未创建工作节点；请创建后重新运行 verify.ps1。" }
foreach ($Name in $Nodes) {
    $Node = (docker inspect $Name | ConvertFrom-Json)[0]
    if ($Node.State.Status -ne "running" -or $Node.State.Health.Status -ne "healthy") { throw "节点异常: $Name" }
    $NodeRevision = $Node.Config.Labels.'org.opencontainers.image.revision'
    if ($ExpectedRevision -and $NodeRevision -ne $ExpectedRevision) { throw "节点镜像不是当前配置版本: $Name" }
    if (-not $ExpectedRevision -and $Node.Config.Image -ne $NodeImage) { throw "节点镜像引用不是当前配置版本: $Name" }
    $Role = $Node.Config.Labels.'io.taskhub.role'
    $Coding = ($Node.Config.Env | Where-Object { $_ -like 'TASKHUB_NODE_CODING_ENABLED=*' }) -split '=', 2 | Select-Object -Last 1
    if ($Role -eq "execution" -and $Coding -ne "true") { throw "执行节点未启用编码能力: $Name" }
    if ($Role -ne "execution" -and $Coding -ne "false") { throw "非执行节点错误启用编码能力: $Name" }
    Write-Host "[通过] 节点 $Name role=$Role coding=$Coding"
}
Write-Host "TaskHub 部署验收通过。"
