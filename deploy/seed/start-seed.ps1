$ErrorActionPreference = "Stop"

$SeedDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$ComposeFile = Join-Path $SeedDirectory "compose.yaml"
$EnvironmentFile = Join-Path $SeedDirectory ".env"
$DockerConfigDirectory = Join-Path $SeedDirectory ".docker-cli"
$ComposeCommand = (Get-Command docker-compose.exe -ErrorAction Stop).Source

function New-HexSecret([int]$ByteCount) {
    $Bytes = New-Object byte[] $ByteCount
    $Generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $Generator.GetBytes($Bytes)
    } finally {
        $Generator.Dispose()
    }
    return ([BitConverter]::ToString($Bytes) -replace '-', '').ToLowerInvariant()
}

function New-FernetKey {
    $Bytes = New-Object byte[] 32
    $Generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $Generator.GetBytes($Bytes)
    } finally {
        $Generator.Dispose()
    }
    return [Convert]::ToBase64String($Bytes).Replace('+', '-').Replace('/', '_')
}

# Docker Desktop's credential helper is unavailable in Windows OpenSSH sessions.
# The seed uses only public base images, so keep a deployment-local anonymous
# client configuration and connect directly to the Linux engine pipe.
New-Item -ItemType Directory -Force $DockerConfigDirectory | Out-Null
'{"auths":{}}' | Set-Content -Path (Join-Path $DockerConfigDirectory "config.json") -Encoding ascii
$env:DOCKER_CONFIG = $DockerConfigDirectory
$env:DOCKER_HOST = "npipe:////./pipe/dockerDesktopLinuxEngine"

docker info *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Docker Desktop is not running or the current user cannot access it."
}

docker image inspect postgres:16-alpine *> $null
if ($LASTEXITCODE -ne 0) {
    docker pull postgres:16-alpine
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to pull postgres:16-alpine. Run pull-public-base-images.ps1 and retry."
    }
}

if (-not (Test-Path $EnvironmentFile)) {
    $Port = 8200
    $PostgresPassword = New-HexSecret 24
    $AdminToken = New-HexSecret 24
    $SessionSecret = New-HexSecret 48
    $ConfigEncryptionKey = New-FernetKey
    $NodeToken = New-HexSecret 32
    @(
        "TASKHUB_PORT=$Port"
        "TASKHUB_BUILD_PROXY="
        "TASKHUB_POSTGRES_PASSWORD=$PostgresPassword"
        "TASKHUB_ADMIN_TOKEN=$AdminToken"
        "TASKHUB_SESSION_SECRET=$SessionSecret"
        "TASKHUB_CONFIG_ENCRYPTION_KEY=$ConfigEncryptionKey"
        "TASKHUB_NODE_TOKEN=$NodeToken"
    ) | Set-Content -Path $EnvironmentFile -Encoding ascii
}

if (-not (Select-String -Path $EnvironmentFile `
        -Pattern '^TASKHUB_CONFIG_ENCRYPTION_KEY=' -Quiet)) {
    "TASKHUB_CONFIG_ENCRYPTION_KEY=$(New-FernetKey)" | Add-Content `
        -Path $EnvironmentFile -Encoding ascii
}

if (-not (Select-String -Path $EnvironmentFile -Pattern '^TASKHUB_NODE_TOKEN=' -Quiet)) {
    "TASKHUB_NODE_TOKEN=$(New-HexSecret 32)" | Add-Content `
        -Path $EnvironmentFile -Encoding ascii
}

if (-not (Select-String -Path $EnvironmentFile -Pattern '^TASKHUB_BUILD_PROXY=' -Quiet)) {
    "TASKHUB_BUILD_PROXY=" | Add-Content `
        -Path $EnvironmentFile -Encoding ascii
}

$PortLine = Get-Content $EnvironmentFile | Where-Object { $_ -match '^TASKHUB_PORT=' }
$Port = if ($PortLine) { ($PortLine -split '=', 2)[1] } else { "8200" }
$BuildProxyLine = Get-Content $EnvironmentFile | Where-Object {
    $_ -match '^TASKHUB_BUILD_PROXY='
}
$BuildProxy = if ($BuildProxyLine) {
    ($BuildProxyLine -split '=', 2)[1]
} else {
    ""
}

$RepositoryRoot = (Resolve-Path (Join-Path $SeedDirectory "..\..")).Path
docker --config $DockerConfigDirectory build --pull=false `
    --build-arg "TASKHUB_VERSION=0.1.0-alpha" `
    --build-arg "HTTP_PROXY=$BuildProxy" `
    --build-arg "HTTPS_PROXY=$BuildProxy" `
    --tag taskhub-v2-seed:0.1.0-alpha `
    --file (Join-Path $RepositoryRoot "Dockerfile") `
    $RepositoryRoot
if ($LASTEXITCODE -ne 0) {
    throw "TaskHub seed image failed to build."
}

& $ComposeCommand --project-directory $SeedDirectory --env-file $EnvironmentFile `
    -f $ComposeFile up -d --no-build --pull never
if ($LASTEXITCODE -ne 0) {
    throw "TaskHub seed containers failed to start."
}

$HealthUrl = "http://127.0.0.1:$Port/api/health"
$Deadline = (Get-Date).AddMinutes(3)
do {
    try {
        $Health = Invoke-RestMethod -Uri $HealthUrl -TimeoutSec 5
        if ($Health.status -eq "ok") {
            Write-Host "TaskHub seed is healthy: http://localhost:$Port"
            Write-Host "For LAN access, replace localhost with this host's IP address."
            Write-Host "The admin token is stored locally in $EnvironmentFile"
            exit 0
        }
    } catch {
        Start-Sleep -Seconds 3
    }
} while ((Get-Date) -lt $Deadline)

    & $ComposeCommand --project-directory $SeedDirectory --env-file $EnvironmentFile `
        -f $ComposeFile ps
    & $ComposeCommand --project-directory $SeedDirectory --env-file $EnvironmentFile `
        -f $ComposeFile logs --tail 80 controller
throw "TaskHub seed health check timed out: $HealthUrl"
