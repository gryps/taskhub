param(
    [Parameter(Mandatory = $true)][string]$Version,
    [string]$OfflineBundle = ""
)
$ErrorActionPreference = "Continue"
$PSDefaultParameterValues["*:ErrorAction"] = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$EnvFile = Join-Path $Root ".env"
$ComposeFile = Join-Path $Root "compose.yaml"
if (-not (Test-Path $EnvFile)) { throw "请在已初始化的 TaskHub 安装目录运行。" }

if ($OfflineBundle) {
    $ChecksumFile = Join-Path $OfflineBundle "SHA256SUMS"
    if (-not (Test-Path $ChecksumFile)) { throw "离线包缺少 SHA256SUMS。" }
    foreach ($Line in Get-Content $ChecksumFile) {
        if (-not $Line.Trim()) { continue }
        $Parts = $Line -split '\s+', 2
        $Target = Join-Path $OfflineBundle $Parts[1].TrimStart('*')
        if ((Get-FileHash -Algorithm SHA256 $Target).Hash.ToLowerInvariant() -ne $Parts[0].ToLowerInvariant()) {
            throw "校验失败: $Target"
        }
    }
    $Manifest = Get-Content (Join-Path $OfflineBundle "manifest.json") -Raw | ConvertFrom-Json
    if ($Manifest.version -ne $Version) { throw "离线包版本 $($Manifest.version) 与目标版本 $Version 不一致。" }
    $Archive = Get-ChildItem (Join-Path $OfflineBundle "images") -Filter "taskhub-images-*.tar" | Select-Object -First 1
    docker load -i $Archive.FullName
    if ($LASTEXITCODE -ne 0) { throw "离线镜像导入失败。" }
} else {
    $Registry = if ($env:TASKHUB_REGISTRY) { $env:TASKHUB_REGISTRY.TrimEnd('/') + "/" } else { "" }
    docker pull "${Registry}taskhub-seed:$Version"
    docker pull "${Registry}taskhub-node:$Version"
}

$BackupOutput = & (Join-Path $Root "backup.ps1")
$BackupLine = ($BackupOutput | Select-String '^备份完成: ' | Select-Object -Last 1).Line
if (-not $BackupLine) { throw "升级前备份失败。" }
$Backup = $BackupLine.Substring(6)
if ($OfflineBundle) {
    Copy-Item (Join-Path $OfflineBundle "compose.yaml") $ComposeFile -Force
}

$Registry = if (-not $OfflineBundle -and $env:TASKHUB_REGISTRY) {
    $env:TASKHUB_REGISTRY.TrimEnd('/') + "/"
} else { "" }
$Lines = Get-Content $EnvFile | ForEach-Object {
    if ($_ -match '^TASKHUB_VERSION=') { "TASKHUB_VERSION=$Version" }
    elseif ($_ -match '^TASKHUB_SEED_IMAGE=') { "TASKHUB_SEED_IMAGE=${Registry}taskhub-seed:$Version" }
    elseif ($_ -match '^TASKHUB_NODE_IMAGE=') { "TASKHUB_NODE_IMAGE=${Registry}taskhub-node:$Version" }
    else { $_ }
}
$Lines | Set-Content $EnvFile -Encoding ascii

docker compose --project-directory $Root --env-file $EnvFile -f $ComposeFile up -d --no-build --pull never
if ($LASTEXITCODE -ne 0) {
    & (Join-Path $Root "restore.ps1") -BackupDirectory $Backup
    throw "升级启动失败，已恢复旧版本。"
}
$PortLine = Get-Content $EnvFile | Where-Object { $_ -match '^TASKHUB_PORT=' } | Select-Object -Last 1
$Port = ($PortLine -split '=', 2)[1]
$HttpsEnabled = (Get-Content $EnvFile | Where-Object { $_ -eq 'TASKHUB_ENFORCE_HTTPS=true' })
$Scheme = if ($HttpsEnabled) { "https" } else { "http" }
if ($HttpsEnabled) { [System.Net.ServicePointManager]::ServerCertificateValidationCallback = { $true } }
$Deadline = (Get-Date).AddMinutes(3)
do {
    try {
        if ((Invoke-RestMethod "${Scheme}://127.0.0.1:$Port/api/health" -TimeoutSec 5).status -eq "ok") {
            Write-Host "升级完成: $Version；恢复点: $Backup"
            exit 0
        }
    } catch { Start-Sleep -Seconds 3 }
} while ((Get-Date) -lt $Deadline)
& (Join-Path $Root "restore.ps1") -BackupDirectory $Backup
throw "升级后健康检查失败，已恢复旧版本。"
