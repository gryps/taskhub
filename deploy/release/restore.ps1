param([Parameter(Mandatory = $true)][string]$BackupDirectory)
$ErrorActionPreference = "Continue"
$PSDefaultParameterValues["*:ErrorAction"] = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$EnvFile = Join-Path $Root ".env"
$ComposeFile = Join-Path $Root "compose.yaml"
if (-not (Test-Path $BackupDirectory)) { throw "备份目录不存在: $BackupDirectory" }
function Get-FileEnvValue([string]$Path, [string]$Name) {
    $Line = Get-Content $Path | Where-Object { $_ -match "^$([regex]::Escape($Name))=" } | Select-Object -Last 1
    if ($Line) { return ($Line -split '=', 2)[1] }
    return ""
}
function Get-KeyFingerprint([string]$Key) {
    if (-not $Key) { throw "备份中的配置加密主密钥为空。" }
    $Bytes = [Text.Encoding]::UTF8.GetBytes("taskhub-backup-v1:$Key")
    $Hash = [Security.Cryptography.SHA256]::Create().ComputeHash($Bytes)
    return ([BitConverter]::ToString($Hash)).Replace("-", "").ToLowerInvariant()
}
foreach ($Line in Get-Content (Join-Path $BackupDirectory "SHA256SUMS")) {
    if (-not $Line.Trim()) { continue }
    $Parts = $Line -split '\s+', 2
    $Target = Join-Path $BackupDirectory $Parts[1].TrimStart('*')
    if ((Get-FileHash -Algorithm SHA256 $Target).Hash.ToLowerInvariant() -ne $Parts[0].ToLowerInvariant()) {
        throw "备份校验失败: $Target"
    }
}
$ExpectedFingerprint = Get-FileEnvValue (Join-Path $BackupDirectory "backup.env") "TASKHUB_CONFIG_KEY_FINGERPRINT"
$BackupKey = Get-FileEnvValue (Join-Path $BackupDirectory ".env") "TASKHUB_CONFIG_ENCRYPTION_KEY"
if ((Get-KeyFingerprint $BackupKey) -ne $ExpectedFingerprint) {
    throw "备份中的配置加密主密钥与备份身份不匹配。"
}
$BackupPostgresImage = Get-FileEnvValue (Join-Path $BackupDirectory "backup.env") "TASKHUB_POSTGRES_IMAGE"
docker load -i (Join-Path $BackupDirectory "images.tar")
if ($LASTEXITCODE -ne 0) { throw "备份镜像导入失败。" }
$IdentityList = docker run --rm -v "${BackupDirectory}:/backup:ro" $BackupPostgresImage `
    pg_restore -l /backup/postgres.dump
if ($LASTEXITCODE -ne 0 -or $IdentityList -notmatch 'taskhub_backup_identity') {
    throw "PostgreSQL 备份缺少加密密钥身份表。"
}
$IdentityData = docker run --rm -v "${BackupDirectory}:/backup:ro" $BackupPostgresImage `
    pg_restore -a -t taskhub_backup_identity -f - /backup/postgres.dump
if ($LASTEXITCODE -ne 0 -or $IdentityData -notmatch [regex]::Escape($ExpectedFingerprint)) {
    throw "PostgreSQL 备份身份与配置加密主密钥不匹配。"
}

docker compose --project-directory $Root --env-file (Join-Path $BackupDirectory ".env") `
    -f (Join-Path $BackupDirectory "compose.yaml") down
Copy-Item (Join-Path $BackupDirectory ".env") $EnvFile -Force
Copy-Item (Join-Path $BackupDirectory "compose.yaml") $ComposeFile -Force
$PostgresLine = Get-Content $EnvFile | Where-Object { $_ -match '^TASKHUB_POSTGRES_IMAGE=' } | Select-Object -Last 1
$PostgresImage = ($PostgresLine -split '=', 2)[1]
$DataLine = Get-Content $EnvFile | Where-Object { $_ -match '^TASKHUB_DATA_VOLUME=' } | Select-Object -Last 1
$DataVolume = ($DataLine -split '=', 2)[1]
$PostgresVolumeLine = Get-Content $EnvFile | Where-Object { $_ -match '^TASKHUB_POSTGRES_VOLUME=' } | Select-Object -Last 1
$PostgresVolume = ($PostgresVolumeLine -split '=', 2)[1]
$AllowedPair = "$DataVolume`:$PostgresVolume"
if ($AllowedPair -notin @(
    "taskhub-data:taskhub-postgres-data",
    "taskhub-seed_taskhub-data:taskhub-seed_postgres-data"
)) { throw "备份中的数据卷名称不在 TaskHub 安全范围内。" }

foreach ($Volume in @($DataVolume, $PostgresVolume)) {
    docker volume create $Volume *> $null
    docker run --rm -v "${Volume}:/data" $PostgresImage `
        sh -c 'find /data -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +'
    if ($LASTEXITCODE -ne 0) { throw "数据卷清理失败: $Volume" }
}
docker run --rm -v "${DataVolume}:/data" -v "${BackupDirectory}:/backup:ro" $PostgresImage `
    sh -c 'tar -xzf /backup/taskhub-data.tar.gz -C /data'
if ($LASTEXITCODE -ne 0) { throw "TaskHub 数据卷恢复失败。" }

docker compose --project-directory $Root --env-file $EnvFile -f $ComposeFile up -d postgres
$PostgresContainer = docker compose --project-directory $Root --env-file $EnvFile -f $ComposeFile ps -q postgres
$Deadline = (Get-Date).AddMinutes(2)
do {
    docker compose --project-directory $Root --env-file $EnvFile -f $ComposeFile `
        exec -T postgres pg_isready -U taskhub -d taskhub *> $null
    if ($LASTEXITCODE -eq 0) { break }
    Start-Sleep -Seconds 2
} while ((Get-Date) -lt $Deadline)
if ((Get-Date) -ge $Deadline) { throw "恢复时 PostgreSQL 未就绪。" }
docker cp (Join-Path $BackupDirectory "postgres.dump") "${PostgresContainer}:/tmp/taskhub.dump"
docker compose --project-directory $Root --env-file $EnvFile -f $ComposeFile `
    exec -T postgres pg_restore -U taskhub -d taskhub --clean --if-exists /tmp/taskhub.dump
if ($LASTEXITCODE -ne 0) { throw "PostgreSQL 恢复失败。" }
$DatabaseFingerprint = docker compose --project-directory $Root --env-file $EnvFile -f $ComposeFile `
    exec -T postgres psql -U taskhub -d taskhub -Atc `
    "SELECT key_fingerprint FROM taskhub_backup_identity WHERE identity_id=1"
if ($LASTEXITCODE -ne 0 -or $DatabaseFingerprint.Trim() -ne $ExpectedFingerprint) {
    throw "恢复后的数据库与配置加密主密钥不匹配，控制器未启动。"
}
docker compose --project-directory $Root --env-file $EnvFile -f $ComposeFile `
    exec -T postgres rm -f /tmp/taskhub.dump
docker compose --project-directory $Root --env-file $EnvFile -f $ComposeFile up -d --no-build --pull never
if ($LASTEXITCODE -ne 0) { throw "恢复后的 TaskHub 启动失败。" }
Write-Host "已从备份恢复: $BackupDirectory"
