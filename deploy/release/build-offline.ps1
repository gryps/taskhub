param([string]$OutputDirectory = "")
$ErrorActionPreference = "Continue"
$PSDefaultParameterValues["*:ErrorAction"] = "Stop"

$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = (Resolve-Path (Join-Path $ScriptRoot "..\..")).Path
$Version = if ($env:TASKHUB_VERSION) { $env:TASKHUB_VERSION } else { "0.1.0-alpha" }
$Platform = if ($env:TASKHUB_PLATFORM) { $env:TASKHUB_PLATFORM } else { "linux/amd64" }
$Arch = ($Platform -split '/', 2)[1]
if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $RepoRoot "dist\taskhub-offline-$Version-$Arch"
}

$env:TASKHUB_REGISTRY = ""
$env:TASKHUB_PUSH = "false"
& (Join-Path $ScriptRoot "build-images.ps1")
if ($LASTEXITCODE -ne 0) { throw "TaskHub 镜像构建失败。" }
docker image inspect postgres:16-alpine *> $null
if ($LASTEXITCODE -ne 0) {
    docker pull --platform $Platform postgres:16-alpine
    if ($LASTEXITCODE -ne 0) { throw "PostgreSQL 镜像拉取失败。" }
}
$ProxyImage = "ghcr.io/tecnativa/docker-socket-proxy:v0.5.0"
docker image inspect $ProxyImage *> $null
if ($LASTEXITCODE -ne 0) {
    docker pull --platform $Platform $ProxyImage
    if ($LASTEXITCODE -ne 0) { throw "Docker Socket Proxy 镜像拉取失败。" }
}

if (Test-Path $OutputDirectory) { throw "输出目录已存在，请移动或删除后重试: $OutputDirectory" }
$ImagesDirectory = Join-Path $OutputDirectory "images"
$DocsDirectory = Join-Path $OutputDirectory "docs"
New-Item -ItemType Directory -Force $ImagesDirectory, $DocsDirectory | Out-Null
Copy-Item (Join-Path $ScriptRoot "README.md"), (Join-Path $ScriptRoot "compose.yaml"), `
    (Join-Path $ScriptRoot ".env.example") $OutputDirectory
Copy-Item (Join-Path $ScriptRoot "*.sh"), (Join-Path $ScriptRoot "*.ps1") $OutputDirectory
Copy-Item (Join-Path $RepoRoot "docs\deployment\ubuntu.md"), `
    (Join-Path $RepoRoot "docs\deployment\windows-docker-desktop.md") $DocsDirectory

$ArchiveName = "taskhub-images-$Version-$Arch.tar"
$ArchivePath = Join-Path $ImagesDirectory $ArchiveName
docker save -o $ArchivePath "taskhub-seed:$Version" "taskhub-node:$Version" postgres:16-alpine $ProxyImage
if ($LASTEXITCODE -ne 0) { throw "离线镜像导出失败。" }

$Manifest = @{
    product = "TaskHub"
    version = $Version
    platform = $Platform
    images = @{
        "taskhub-seed:$Version" = (docker image inspect --format '{{.Id}}' "taskhub-seed:$Version")
        "taskhub-node:$Version" = (docker image inspect --format '{{.Id}}' "taskhub-node:$Version")
        "postgres:16-alpine" = (docker image inspect --format '{{.Id}}' postgres:16-alpine)
    }
}
$Manifest.images[$ProxyImage] = (docker image inspect --format '{{.Id}}' $ProxyImage)
$ManifestPath = Join-Path $OutputDirectory "manifest.json"
$Manifest | ConvertTo-Json -Depth 4 | Set-Content $ManifestPath -Encoding utf8
$ChecksumLines = foreach ($Path in Get-ChildItem $OutputDirectory -Recurse -File | Sort-Object FullName) {
    if ($Path.Name -eq "SHA256SUMS") { continue }
    $RelativePath = $Path.FullName.Substring($OutputDirectory.Length).TrimStart('\', '/').Replace('\', '/')
    "$((Get-FileHash -Algorithm SHA256 $Path.FullName).Hash.ToLowerInvariant())  $RelativePath"
}
$ChecksumLines | Set-Content (Join-Path $OutputDirectory "SHA256SUMS") -Encoding ascii
Write-Host "离线交付包已生成: $OutputDirectory"
