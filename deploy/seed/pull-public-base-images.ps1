$ErrorActionPreference = "Stop"

$DockerConfig = Join-Path $env:USERPROFILE ".docker\config.json"
$DockerConfigExists = Test-Path $DockerConfig
$OriginalConfig = if ($DockerConfigExists) {
    [System.IO.File]::ReadAllBytes($DockerConfig)
} else {
    $null
}
$PostgresMirror = "m.daocloud.io/docker.io/library/postgres:16-alpine"
$PythonMirror = "m.daocloud.io/docker.io/library/python:3.12-slim-bookworm"

try {
    @{
        auths = @{
            "https://index.docker.io/v1/" = @{ auth = "Og==" }
        }
        currentContext = "desktop-linux"
    } | ConvertTo-Json | Set-Content -Path $DockerConfig -Encoding ascii

    docker pull $PostgresMirror
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to pull postgres:16-alpine"
    }
    docker tag $PostgresMirror postgres:16-alpine

    docker pull $PythonMirror
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to pull python:3.12-slim-bookworm"
    }
    docker tag $PythonMirror python:3.12-slim-bookworm
} finally {
    if ($DockerConfigExists) {
        [System.IO.File]::WriteAllBytes($DockerConfig, $OriginalConfig)
    } elseif (Test-Path $DockerConfig) {
        Remove-Item $DockerConfig
    }
}
