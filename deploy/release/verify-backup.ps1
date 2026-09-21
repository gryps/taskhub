param([Parameter(Mandatory = $true)][string]$BackupDirectory)
$ErrorActionPreference = "Continue"
$PSDefaultParameterValues["*:ErrorAction"] = "Stop"

$BackupDirectory = (Resolve-Path $BackupDirectory).Path
$Required = @(".env", "backup.env", "postgres.dump", "taskhub-data.tar.gz", "images.tar", "compose.yaml", "SHA256SUMS")
foreach ($Name in $Required) {
    if (-not (Test-Path (Join-Path $BackupDirectory $Name) -PathType Leaf)) {
        throw "备份缺少文件: $Name"
    }
}

foreach ($Line in (Get-Content (Join-Path $BackupDirectory "SHA256SUMS"))) {
    if ($Line -notmatch '^([a-fA-F0-9]{64})\s+(.+)$') { throw "SHA256SUMS 格式无效。" }
    $Expected = $Matches[1].ToLowerInvariant()
    $Name = $Matches[2]
    $Actual = (Get-FileHash -Algorithm SHA256 (Join-Path $BackupDirectory $Name)).Hash.ToLowerInvariant()
    if ($Actual -ne $Expected) { throw "备份文件摘要不匹配: $Name" }
}

function Get-EnvValue([string]$Path, [string]$Name) {
    $Line = Get-Content $Path | Where-Object { $_ -match "^$([regex]::Escape($Name))=" } | Select-Object -Last 1
    if ($Line) { return ($Line -split '=', 2)[1] }
    return ""
}

function Get-KeyFingerprint([string]$Key) {
    if (-not $Key) { throw "配置加密主密钥为空。" }
    $Bytes = [Text.Encoding]::UTF8.GetBytes("taskhub-backup-v1:$Key")
    $Hash = [Security.Cryptography.SHA256]::Create().ComputeHash($Bytes)
    return ([BitConverter]::ToString($Hash)).Replace("-", "").ToLowerInvariant()
}

$BackupKey = Get-EnvValue (Join-Path $BackupDirectory ".env") "TASKHUB_CONFIG_ENCRYPTION_KEY"
$ExpectedFingerprint = Get-EnvValue (Join-Path $BackupDirectory "backup.env") "TASKHUB_CONFIG_KEY_FINGERPRINT"
if ((Get-KeyFingerprint $BackupKey) -ne $ExpectedFingerprint) {
    throw "备份中的配置加密主密钥与备份身份不匹配。"
}
$PostgresImage = Get-EnvValue (Join-Path $BackupDirectory "backup.env") "TASKHUB_POSTGRES_IMAGE"
if (-not $PostgresImage) { $PostgresImage = "postgres:16-alpine" }
docker image inspect $PostgresImage *> $null
if ($LASTEXITCODE -ne 0) {
    docker load -i (Join-Path $BackupDirectory "images.tar") *> $null
    if ($LASTEXITCODE -ne 0) { throw "备份镜像归档无法加载。" }
}
$Catalog = docker run --rm -v "${BackupDirectory}:/backup:ro" $PostgresImage pg_restore -l /backup/postgres.dump
if ($LASTEXITCODE -ne 0 -or ($Catalog -join "`n") -notmatch "taskhub_backup_identity") {
    throw "PostgreSQL 备份缺少加密密钥身份表。"
}
$Identity = docker run --rm -v "${BackupDirectory}:/backup:ro" $PostgresImage `
    pg_restore -a -t taskhub_backup_identity -f - /backup/postgres.dump
if ($LASTEXITCODE -ne 0 -or ($Identity -join "`n") -notmatch ([regex]::Escape($ExpectedFingerprint))) {
    throw "PostgreSQL 备份身份与配置加密主密钥不匹配。"
}
docker run --rm -v "${BackupDirectory}:/backup:ro" $PostgresImage `
    tar -tzf /backup/taskhub-data.tar.gz *> $null
if ($LASTEXITCODE -ne 0) { throw "TaskHub 数据归档无法读取。" }
Write-Output "备份验证通过: $BackupDirectory"
