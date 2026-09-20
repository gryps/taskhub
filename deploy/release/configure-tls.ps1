param(
    [string]$Hostname = "",
    [string]$IpAddress = "",
    [string]$Certificate = "",
    [string]$PrivateKey = ""
)
$ErrorActionPreference = "Continue"
$PSDefaultParameterValues["*:ErrorAction"] = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$EnvFile = Join-Path $Root ".env"
$TlsDirectory = Join-Path $Root "tls"
New-Item -ItemType Directory -Force $TlsDirectory | Out-Null
if (-not (Test-Path $EnvFile)) { throw "缺少 $EnvFile；请先复制 .env.example 或运行 init.ps1。" }
$SeedLine = Get-Content $EnvFile | Where-Object { $_ -like 'TASKHUB_SEED_IMAGE=*' } | Select-Object -Last 1
$SeedImage = ($SeedLine -split '=', 2)[1]
if (-not $SeedImage) { throw "TASKHUB_SEED_IMAGE 未配置。" }
docker image inspect $SeedImage *> $null
if ($LASTEXITCODE -ne 0) { docker pull $SeedImage }
if ($LASTEXITCODE -ne 0) { throw "Seed 镜像不可用，无法运行 OpenSSL。" }

$TargetCert = Join-Path $TlsDirectory "taskhub.crt"
$TargetKey = Join-Path $TlsDirectory "taskhub.key"
if ($Certificate -or $PrivateKey) {
    if (-not (Test-Path $Certificate) -or -not (Test-Path $PrivateKey)) { throw "Certificate 与 PrivateKey 必须同时指向有效文件。" }
    Copy-Item $Certificate (Join-Path $TlsDirectory "candidate.crt") -Force
    Copy-Item $PrivateKey (Join-Path $TlsDirectory "candidate.key") -Force
    $CertPublic = docker run --rm --entrypoint sh -v "${TlsDirectory}:/tls" $SeedImage -c "openssl x509 -in /tls/candidate.crt -pubkey -noout | openssl sha256"
    if ($LASTEXITCODE -ne 0) { throw "证书格式无效。" }
    $KeyPublic = docker run --rm --entrypoint sh -v "${TlsDirectory}:/tls" $SeedImage -c "openssl pkey -in /tls/candidate.key -pubout | openssl sha256"
    if ($LASTEXITCODE -ne 0 -or $CertPublic -ne $KeyPublic) { throw "证书与私钥不匹配。" }
    Move-Item (Join-Path $TlsDirectory "candidate.crt") $TargetCert -Force
    Move-Item (Join-Path $TlsDirectory "candidate.key") $TargetKey -Force
} else {
    if (-not $Hostname) { throw "必须提供 -Hostname，或同时提供 -Certificate/-PrivateKey。" }
    if ($Hostname -notmatch '^[A-Za-z0-9.-]+$') { throw "主机名格式无效。" }
    $San = "DNS:$Hostname,DNS:localhost,IP:127.0.0.1"
    if ($IpAddress) {
        if ($IpAddress -notmatch '^[0-9A-Fa-f:.]+$') { throw "IP 地址格式无效。" }
        $San += ",IP:$IpAddress"
    }
    docker run --rm --entrypoint openssl -v "${TlsDirectory}:/tls" $SeedImage req -x509 -newkey rsa:3072 -nodes -days 397 `
        -keyout /tls/taskhub.key -out /tls/taskhub.crt -subj "/CN=$Hostname" -addext "subjectAltName=$San"
    if ($LASTEXITCODE -ne 0) { throw "TLS 证书生成失败。" }
}
Write-Host "TLS 文件已写入 $TlsDirectory。若 TaskHub 已运行，请执行: docker compose --env-file .env -f compose.yaml up -d --force-recreate controller"
