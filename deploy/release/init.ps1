$ErrorActionPreference = "Continue"
$PSDefaultParameterValues["*:ErrorAction"] = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$EnvFile = Join-Path $Root ".env"
$ComposeFile = Join-Path $Root "compose.yaml"

function New-HexSecret([int]$ByteCount) {
    $Bytes = New-Object byte[] $ByteCount
    $Generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $Generator.GetBytes($Bytes) } finally { $Generator.Dispose() }
    return ([BitConverter]::ToString($Bytes) -replace '-', '').ToLowerInvariant()
}

function New-FernetKey {
    $Bytes = New-Object byte[] 32
    $Generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $Generator.GetBytes($Bytes) } finally { $Generator.Dispose() }
    return [Convert]::ToBase64String($Bytes).Replace('+', '-').Replace('/', '_')
}

function Get-EnvValue([string]$Name) {
    $Line = Get-Content $EnvFile | Where-Object { $_ -match "^$([regex]::Escape($Name))=" } | Select-Object -Last 1
    if ($Line) { return ($Line -split '=', 2)[1] }
    return ""
}

function Set-EnvValue([string]$Name, [string]$Value) {
    $Found = $false
    $Lines = Get-Content $EnvFile | ForEach-Object {
        if ($_ -match "^$([regex]::Escape($Name))=") {
            $Found = $true
            "$Name=$Value"
        } else { $_ }
    }
    if (-not $Found) { $Lines += "$Name=$Value" }
    $Lines | Set-Content $EnvFile -Encoding ascii
}

function Ensure-EnvValue([string]$Name, [string]$Default) {
    $Exists = Get-Content $EnvFile | Where-Object { $_ -match "^$([regex]::Escape($Name))=" } | Select-Object -First 1
    if (-not $Exists) { Set-EnvValue $Name $Default }
}

function Ensure-Secret([string]$Name, [string]$Value) {
    $Current = Get-EnvValue $Name
    if (-not $Current -or $Current -like "replace-with-*") { Set-EnvValue $Name $Value }
}

docker info *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Docker Desktop 未启动，或当前用户无权访问 Docker。脚本不会请求 sudo 密码。"
}
docker compose version *> $null
if ($LASTEXITCODE -ne 0) { throw "需要 Docker Compose v2（docker compose）。" }

if (-not (Test-Path $EnvFile)) {
    New-Item -ItemType File $EnvFile | Out-Null
    Write-Host "已生成主机本地配置 $EnvFile"
}

$DataVolume = "taskhub-data"
$PostgresVolume = "taskhub-postgres-data"
docker volume inspect taskhub-data *> $null
$StandardDataExists = $LASTEXITCODE -eq 0
docker volume inspect taskhub-postgres-data *> $null
$StandardPostgresExists = $LASTEXITCODE -eq 0
docker volume inspect taskhub-seed_taskhub-data *> $null
$LegacyDataExists = $LASTEXITCODE -eq 0
docker volume inspect taskhub-seed_postgres-data *> $null
$LegacyPostgresExists = $LASTEXITCODE -eq 0
$LegacyDataInUse = [bool](docker ps -aq --filter "volume=taskhub-seed_taskhub-data")
$LegacyPostgresInUse = [bool](docker ps -aq --filter "volume=taskhub-seed_postgres-data")
if ($LegacyDataExists -and $LegacyPostgresExists -and (
    (-not $StandardDataExists -and -not $StandardPostgresExists) -or
    ($LegacyDataInUse -and $LegacyPostgresInUse)
)) {
    $DataVolume = "taskhub-seed_taskhub-data"
    $PostgresVolume = "taskhub-seed_postgres-data"
}

Ensure-EnvValue "TASKHUB_VERSION" "0.1.0-alpha"
Ensure-EnvValue "TASKHUB_PORT" "8200"
Ensure-EnvValue "TASKHUB_SEED_IMAGE" "ghcr.io/gryps/taskhub-seed:0.1.0-alpha"
Ensure-EnvValue "TASKHUB_NODE_IMAGE" "ghcr.io/gryps/taskhub-node:0.1.0-alpha"
Ensure-EnvValue "TASKHUB_POSTGRES_IMAGE" "postgres:16-alpine"
Ensure-EnvValue "TASKHUB_DOCKER_PROXY_IMAGE" "ghcr.io/tecnativa/docker-socket-proxy:v0.5.0"
Ensure-EnvValue "TASKHUB_DATA_VOLUME" $DataVolume
Ensure-EnvValue "TASKHUB_POSTGRES_VOLUME" $PostgresVolume
Ensure-EnvValue "TASKHUB_COOKIE_SECURE" "true"
Ensure-EnvValue "TASKHUB_ENFORCE_HTTPS" "true"
Ensure-EnvValue "TASKHUB_TLS_DIRECTORY" "./tls"
Ensure-EnvValue "TASKHUB_TLS_CERT_FILE" "/run/taskhub/tls/taskhub.crt"
Ensure-EnvValue "TASKHUB_TLS_KEY_FILE" "/run/taskhub/tls/taskhub.key"
Ensure-EnvValue "TASKHUB_TRUSTED_HOSTS" "*"
Ensure-EnvValue "TASKHUB_SESSION_IDLE_SECONDS" "1800"
Ensure-EnvValue "TASKHUB_SESSION_ABSOLUTE_SECONDS" "43200"
Ensure-EnvValue "TASKHUB_LOGIN_MAX_FAILURES" "5"
Ensure-EnvValue "TASKHUB_LOGIN_WINDOW_SECONDS" "900"
Ensure-EnvValue "TASKHUB_LOGIN_LOCK_SECONDS" "900"
Ensure-EnvValue "TASKHUB_OPENAI_PROXY_URL" ""
Ensure-EnvValue "TASKHUB_WORKER_MODE" "git"
Ensure-Secret "TASKHUB_POSTGRES_PASSWORD" (New-HexSecret 24)
Ensure-Secret "TASKHUB_ADMIN_TOKEN" (New-HexSecret 24)
Ensure-Secret "TASKHUB_SESSION_SECRET" (New-HexSecret 48)
Ensure-Secret "TASKHUB_CONFIG_ENCRYPTION_KEY" (New-FernetKey)

$Archive = Get-ChildItem (Join-Path $Root "images") -Filter "taskhub-images-*.tar" -ErrorAction SilentlyContinue | Select-Object -First 1
if ($Archive) {
    $ChecksumFile = Join-Path $Root "SHA256SUMS"
    if (-not (Test-Path $ChecksumFile)) { throw "离线包缺少 SHA256SUMS。" }
    foreach ($Line in Get-Content $ChecksumFile) {
        if (-not $Line.Trim()) { continue }
        $Parts = $Line -split '\s+', 2
        $Target = Join-Path $Root $Parts[1].TrimStart('*')
        $Actual = (Get-FileHash -Algorithm SHA256 $Target).Hash.ToLowerInvariant()
        if ($Actual -ne $Parts[0].ToLowerInvariant()) { throw "校验失败: $Target" }
    }
    $OfflineVersion = Get-EnvValue "TASKHUB_VERSION"
    Set-EnvValue "TASKHUB_SEED_IMAGE" "taskhub-seed:$OfflineVersion"
    Set-EnvValue "TASKHUB_NODE_IMAGE" "taskhub-node:$OfflineVersion"
    $Manifest = Get-Content (Join-Path $Root "manifest.json") -Raw | ConvertFrom-Json
    $ServerArch = docker version --format '{{.Server.Arch}}'
    if ($ServerArch -eq "x86_64") { $ServerArch = "amd64" }
    if ($ServerArch -eq "aarch64") { $ServerArch = "arm64" }
    if ($Manifest.platform -ne "linux/$ServerArch") {
        throw "离线包平台 $($Manifest.platform) 与 Docker 主机 linux/$ServerArch 不匹配。"
    }
    docker load -i $Archive.FullName
    if ($LASTEXITCODE -ne 0) { throw "离线镜像导入失败。" }
} else {
    foreach ($Image in @(
        (Get-EnvValue "TASKHUB_SEED_IMAGE"),
        (Get-EnvValue "TASKHUB_NODE_IMAGE"),
        (Get-EnvValue "TASKHUB_POSTGRES_IMAGE"),
        (Get-EnvValue "TASKHUB_DOCKER_PROXY_IMAGE")
    )) {
        docker image inspect $Image *> $null
        if ($LASTEXITCODE -ne 0) {
            docker pull $Image
            if ($LASTEXITCODE -ne 0) { throw "镜像拉取失败: $Image" }
        }
    }
}

$ServerArch = docker version --format '{{.Server.Arch}}'
if ($ServerArch -eq "x86_64") { $ServerArch = "amd64" }
if ($ServerArch -eq "aarch64") { $ServerArch = "arm64" }
foreach ($Image in @(
    (Get-EnvValue "TASKHUB_SEED_IMAGE"),
    (Get-EnvValue "TASKHUB_NODE_IMAGE"),
    (Get-EnvValue "TASKHUB_POSTGRES_IMAGE"),
    (Get-EnvValue "TASKHUB_DOCKER_PROXY_IMAGE")
)) {
    $Actual = docker image inspect --format '{{.Os}}/{{.Architecture}}' $Image
    if ($Actual -ne "linux/$ServerArch") {
        throw "镜像架构不匹配: $Image 是 $Actual，Docker 主机是 linux/$ServerArch。"
    }
}

$TlsDirectory = Join-Path $Root "tls"
New-Item -ItemType Directory -Force $TlsDirectory | Out-Null
$TlsCertificate = Join-Path $TlsDirectory "taskhub.crt"
$TlsKey = Join-Path $TlsDirectory "taskhub.key"
if (-not (Test-Path $TlsCertificate) -or -not (Test-Path $TlsKey)) {
    $SeedImage = Get-EnvValue "TASKHUB_SEED_IMAGE"
    docker run --rm --entrypoint openssl -v "${TlsDirectory}:/tls" $SeedImage req -x509 `
        -newkey rsa:3072 -nodes -days 397 -keyout /tls/taskhub.key -out /tls/taskhub.crt `
        -subj /CN=taskhub.local -addext "subjectAltName=DNS:taskhub.local,DNS:localhost,IP:127.0.0.1"
    if ($LASTEXITCODE -ne 0) { throw "生成初始 TLS 证书失败。" }
}

docker compose --project-directory $Root --env-file $EnvFile -f $ComposeFile config *> $null
if ($LASTEXITCODE -ne 0) { throw "Compose 配置无效。" }
docker compose --project-directory $Root --env-file $EnvFile -f $ComposeFile up -d --no-build --pull never
if ($LASTEXITCODE -ne 0) { throw "TaskHub Seed 启动失败。" }

$Port = Get-EnvValue "TASKHUB_PORT"
$Deadline = (Get-Date).AddMinutes(3)
$Scheme = if ((Get-EnvValue "TASKHUB_ENFORCE_HTTPS") -eq "true") { "https" } else { "http" }
if ($Scheme -eq "https") { [System.Net.ServicePointManager]::ServerCertificateValidationCallback = { $true } }
do {
    try {
        $Health = Invoke-RestMethod "${Scheme}://127.0.0.1:$Port/api/health" -TimeoutSec 5
        if ($Health.status -eq "ok") {
            Write-Host "TaskHub Seed 已启动: ${Scheme}://127.0.0.1:$Port"
            Write-Host "首次设置口令保存在 $EnvFile 的 TASKHUB_ADMIN_TOKEN。"
            exit 0
        }
    } catch { Start-Sleep -Seconds 3 }
} while ((Get-Date) -lt $Deadline)

docker compose --project-directory $Root --env-file $EnvFile -f $ComposeFile ps
docker compose --project-directory $Root --env-file $EnvFile -f $ComposeFile logs --tail 80 controller
throw "TaskHub Seed 健康检查超时。"
