param([Parameter(Mandatory = $true)][string]$BackupDirectory)
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$EnvFile = Join-Path $Root ".env"
$ComposeFile = Join-Path $Root "compose.yaml"
if (-not (Test-Path $BackupDirectory)) { throw "备份目录不存在: $BackupDirectory" }
foreach ($Line in Get-Content (Join-Path $BackupDirectory "SHA256SUMS")) {
    if (-not $Line.Trim()) { continue }
    $Parts = $Line -split '\s+', 2
    $Target = Join-Path $BackupDirectory $Parts[1].TrimStart('*')
    if ((Get-FileHash -Algorithm SHA256 $Target).Hash.ToLowerInvariant() -ne $Parts[0].ToLowerInvariant()) {
        throw "备份校验失败: $Target"
    }
}

docker compose --project-directory $Root --env-file (Join-Path $BackupDirectory ".env") `
    -f (Join-Path $BackupDirectory "compose.yaml") down
docker load -i (Join-Path $BackupDirectory "images.tar")
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
docker compose --project-directory $Root --env-file $EnvFile -f $ComposeFile `
    exec -T postgres rm -f /tmp/taskhub.dump
docker compose --project-directory $Root --env-file $EnvFile -f $ComposeFile up -d --no-build --pull never
if ($LASTEXITCODE -ne 0) { throw "恢复后的 TaskHub 启动失败。" }
Write-Host "已从备份恢复: $BackupDirectory"
