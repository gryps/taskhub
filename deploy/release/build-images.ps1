$ErrorActionPreference = "Continue"
$PSDefaultParameterValues["*:ErrorAction"] = "Stop"

$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = (Resolve-Path (Join-Path $ScriptRoot "..\..")).Path
$Version = if ($env:TASKHUB_VERSION) { $env:TASKHUB_VERSION } else { "0.1.0-alpha" }
$CodexVersion = if ($env:CODEX_VERSION) { $env:CODEX_VERSION } else { "0.153.4" }
$Platform = if ($env:TASKHUB_PLATFORM) { $env:TASKHUB_PLATFORM } else { "linux/amd64" }
$Registry = if ($env:TASKHUB_REGISTRY) { $env:TASKHUB_REGISTRY.TrimEnd('/') + "/" } else { "" }
$MirrorArgs = @()
if ($env:DEBIAN_MIRROR) { $MirrorArgs += @("--build-arg", "DEBIAN_MIRROR=$($env:DEBIAN_MIRROR)") }
if ($env:DEBIAN_SECURITY_MIRROR) { $MirrorArgs += @("--build-arg", "DEBIAN_SECURITY_MIRROR=$($env:DEBIAN_SECURITY_MIRROR)") }
$Commit = (git -C $RepoRoot rev-parse HEAD 2>$null)
if (-not $Commit) { $Commit = "unknown" }
$SeedImage = "${Registry}taskhub-seed:$Version"
$NodeImage = "${Registry}taskhub-node:$Version"

docker info *> $null
if ($LASTEXITCODE -ne 0) { throw "Docker 未启动。" }
docker buildx version *> $null
if ($LASTEXITCODE -ne 0) { throw "Docker Buildx 不可用。" }

foreach ($Build in @(
    @{ File = "Dockerfile"; Image = $SeedImage },
    @{ File = "deploy/node/Dockerfile"; Image = $NodeImage }
)) {
    docker buildx build --load --pull --platform $Platform `
        --build-arg "TASKHUB_VERSION=$Version" `
        --build-arg "TASKHUB_COMMIT=$Commit" `
        --build-arg "CODEX_VERSION=$CodexVersion" `
        @MirrorArgs `
        --tag $Build.Image --file (Join-Path $RepoRoot $Build.File) $RepoRoot
    if ($LASTEXITCODE -ne 0) { throw "镜像构建失败: $($Build.Image)" }
}

if ($env:TASKHUB_PUSH -eq "true") {
    if (-not $Registry) { throw "TASKHUB_PUSH=true 时必须设置 TASKHUB_REGISTRY。" }
    docker push $SeedImage
    docker push $NodeImage
}
Write-Host "Seed: $SeedImage"
Write-Host "Node: $NodeImage"
Write-Host "Platform: $Platform"
