param([string]$BackupDirectory = "")
$ErrorActionPreference = "Continue"
$PSDefaultParameterValues["*:ErrorAction"] = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$EnvFile = Join-Path $Root ".env"
$ComposeFile = Join-Path $Root "compose.yaml"
$Timestamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
if (-not $BackupDirectory) { $BackupDirectory = Join-Path $Root "backups\$Timestamp" }
if (-not (Test-Path $EnvFile)) { throw "缺少 $EnvFile" }
if (Test-Path $BackupDirectory) { throw "备份目录已存在: $BackupDirectory" }
New-Item -ItemType Directory -Force $BackupDirectory | Out-Null
Copy-Item $EnvFile (Join-Path $BackupDirectory ".env")
Copy-Item $ComposeFile (Join-Path $BackupDirectory "compose.yaml")

function Get-EnvValue([string]$Name) {
    $Line = Get-Content $EnvFile | Where-Object { $_ -match "^$([regex]::Escape($Name))=" } | Select-Object -Last 1
    if ($Line) { return ($Line -split '=', 2)[1] }
    return ""
}

function Get-KeyFingerprint([string]$Key) {
    if (-not $Key) { throw "配置加密主密钥为空，拒绝生成不可验证备份。" }
    $Bytes = [Text.Encoding]::UTF8.GetBytes("taskhub-backup-v1:$Key")
    $Hash = [Security.Cryptography.SHA256]::Create().ComputeHash($Bytes)
    return ([BitConverter]::ToString($Hash)).Replace("-", "").ToLowerInvariant()
}

docker compose --project-directory $Root --env-file $EnvFile -f $ComposeFile stop controller
if ($LASTEXITCODE -ne 0) { throw "控制器停止失败。" }
try {
    docker compose --project-directory $Root --env-file $EnvFile -f $ComposeFile `
        exec -T postgres pg_dump -U taskhub -d taskhub -Fc -f /tmp/taskhub.dump
    if ($LASTEXITCODE -ne 0) { throw "PostgreSQL 备份失败。" }
    $PostgresContainer = docker compose --project-directory $Root --env-file $EnvFile `
        -f $ComposeFile ps -q postgres
    docker cp "${PostgresContainer}:/tmp/taskhub.dump" (Join-Path $BackupDirectory "postgres.dump")
    docker compose --project-directory $Root --env-file $EnvFile -f $ComposeFile `
        exec -T postgres rm -f /tmp/taskhub.dump

    $PostgresImage = Get-EnvValue "TASKHUB_POSTGRES_IMAGE"
    $SeedImage = Get-EnvValue "TASKHUB_SEED_IMAGE"
    $NodeImage = Get-EnvValue "TASKHUB_NODE_IMAGE"
    $DataVolume = Get-EnvValue "TASKHUB_DATA_VOLUME"
    $PostgresVolume = Get-EnvValue "TASKHUB_POSTGRES_VOLUME"
    $KeyFingerprint = Get-KeyFingerprint (Get-EnvValue "TASKHUB_CONFIG_ENCRYPTION_KEY")
    if ("$DataVolume`:$PostgresVolume" -notin @(
        "taskhub-data:taskhub-postgres-data",
        "taskhub-seed_taskhub-data:taskhub-seed_postgres-data"
    )) { throw "数据卷名称不在 TaskHub 安全范围内。" }
    docker run --rm -v "${DataVolume}:/data" -v "${BackupDirectory}:/backup" $PostgresImage `
        sh -c 'tar -czf /backup/taskhub-data.tar.gz -C /data .'
    if ($LASTEXITCODE -ne 0) { throw "TaskHub 数据卷备份失败。" }
    docker save -o (Join-Path $BackupDirectory "images.tar") $SeedImage $NodeImage $PostgresImage
    if ($LASTEXITCODE -ne 0) { throw "回退镜像导出失败。" }

    @(
        "TASKHUB_BACKUP_CREATED=$Timestamp"
        "TASKHUB_SEED_IMAGE=$SeedImage"
        "TASKHUB_NODE_IMAGE=$NodeImage"
        "TASKHUB_POSTGRES_IMAGE=$PostgresImage"
        "TASKHUB_CONFIG_KEY_FINGERPRINT=$KeyFingerprint"
    ) | Set-Content (Join-Path $BackupDirectory "backup.env") -Encoding ascii
    $Checksums = foreach ($Name in @(".env", "backup.env", "postgres.dump", "taskhub-data.tar.gz", "images.tar", "compose.yaml")) {
        $Hash = (Get-FileHash -Algorithm SHA256 (Join-Path $BackupDirectory $Name)).Hash.ToLowerInvariant()
        "$Hash  $Name"
    }
    $Checksums | Set-Content (Join-Path $BackupDirectory "SHA256SUMS") -Encoding ascii
} finally {
    docker compose --project-directory $Root --env-file $EnvFile -f $ComposeFile start controller *> $null
}
Write-Output "备份完成: $BackupDirectory"
