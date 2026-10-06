$ErrorActionPreference = "Continue"
$PSDefaultParameterValues["*:ErrorAction"] = "Stop"

$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = (Resolve-Path (Join-Path $ScriptRoot "..\..")).Path
$Version = if ($env:TASKHUB_VERSION) { $env:TASKHUB_VERSION } else { "0.1.0-alpha" }
$CodexVersion = if ($env:CODEX_VERSION) { $env:CODEX_VERSION } else { "0.153.4" }
$Platform = if ($env:TASKHUB_PLATFORM) { $env:TASKHUB_PLATFORM } else { "linux/amd64" }
$Registry = if ($env:TASKHUB_REGISTRY) { $env:TASKHUB_REGISTRY.TrimEnd('/') + "/" } else { "" }
$ReuseRuntimeVersion = $env:TASKHUB_REUSE_RUNTIME_VERSION
$PullArgs = @()
if ($env:TASKHUB_PULL_BASE_IMAGES -eq "true") { $PullArgs += "--pull" }
$MirrorArgs = @()
if ($env:TASKHUB_BUILD_PROXY) {
    $MirrorArgs += @("--build-arg", "HTTP_PROXY=$($env:TASKHUB_BUILD_PROXY)")
    $MirrorArgs += @("--build-arg", "HTTPS_PROXY=$($env:TASKHUB_BUILD_PROXY)")
}
if ($env:NPM_REGISTRY) { $MirrorArgs += @("--build-arg", "NPM_REGISTRY=$($env:NPM_REGISTRY)") }
if ($env:PYPI_INDEX_URL) { $MirrorArgs += @("--build-arg", "PYPI_INDEX_URL=$($env:PYPI_INDEX_URL)") }
if ($env:DEBIAN_MIRROR) { $MirrorArgs += @("--build-arg", "DEBIAN_MIRROR=$($env:DEBIAN_MIRROR)") }
if ($env:DEBIAN_SECURITY_MIRROR) { $MirrorArgs += @("--build-arg", "DEBIAN_SECURITY_MIRROR=$($env:DEBIAN_SECURITY_MIRROR)") }
$Commit = if ($env:TASKHUB_COMMIT) {
    $env:TASKHUB_COMMIT
} else {
    (git -C $RepoRoot rev-parse HEAD 2>$null)
}
if (-not $Commit) { $Commit = "unknown" }
$SeedImage = "${Registry}taskhub-seed:$Version"
$NodeImage = "${Registry}taskhub-node:$Version"

docker info *> $null
if ($LASTEXITCODE -ne 0) { throw "Docker 未启动。" }
docker buildx version *> $null
if ($LASTEXITCODE -ne 0) { throw "Docker Buildx 不可用。" }

if ($ReuseRuntimeVersion) {
    $BaseSeed = "${Registry}taskhub-seed:$ReuseRuntimeVersion"
    $BaseNode = "${Registry}taskhub-node:$ReuseRuntimeVersion"
    foreach ($Image in @($BaseSeed, $BaseNode)) {
        docker image inspect $Image *> $null
        if ($LASTEXITCODE -ne 0) { throw "缺少本地运行时基础镜像: $Image" }
    }
    $BaseCommit = docker image inspect $BaseSeed --format '{{index .Config.Labels "org.opencontainers.image.revision"}}'
    git -C $RepoRoot cat-file -e "$BaseCommit^{commit}" 2>$null
    if ($LASTEXITCODE -ne 0) { throw "无法验证复用镜像对应的源码提交。" }
    git -C $RepoRoot diff --quiet $BaseCommit HEAD -- pyproject.toml Dockerfile deploy/node/Dockerfile deploy/docker/entrypoint.sh
    if ($LASTEXITCODE -ne 0) { throw "依赖或运行时定义已变化，不能复用旧运行时；请执行完整构建。" }
}

foreach ($Build in @(
    @{ File = "Dockerfile"; Image = $SeedImage; Repository = "taskhub-seed"; Title = "TaskHub V2 Seed Controller" },
    @{ File = "deploy/node/Dockerfile"; Image = $NodeImage; Repository = "taskhub-node"; Title = "TaskHub Unified Worker Node" }
)) {
    if ($ReuseRuntimeVersion) {
        docker buildx build --load --provenance=false --platform $Platform `
            --build-arg "BASE_IMAGE=${Registry}$($Build.Repository):$ReuseRuntimeVersion" `
            --build-arg "IMAGE_TITLE=$($Build.Title)" `
            --build-arg "TASKHUB_VERSION=$Version" `
            --build-arg "TASKHUB_COMMIT=$Commit" `
            --tag $Build.Image --file (Join-Path $ScriptRoot "Dockerfile.incremental") $RepoRoot
    } else {
        docker buildx build --load @PullArgs --provenance=false --platform $Platform `
            --build-arg "TASKHUB_VERSION=$Version" `
            --build-arg "TASKHUB_COMMIT=$Commit" `
            --build-arg "CODEX_VERSION=$CodexVersion" `
            @MirrorArgs `
            --tag $Build.Image --file (Join-Path $RepoRoot $Build.File) $RepoRoot
    }
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
