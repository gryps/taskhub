param(
    [string]$Version = "0.1.0-alpha",
    [string]$OutputDirectory = "",
    [string]$Archive = ""
)
$ErrorActionPreference = "Continue"
$PSDefaultParameterValues["*:ErrorAction"] = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = (Resolve-Path (Join-Path $ScriptRoot "..\..")).Path
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $RepoRoot "dist\taskhub-release-$Version" }
if (-not $Archive) { $Archive = "$OutputDirectory.zip" }
if (Test-Path $OutputDirectory) { throw "输出目录已存在: $OutputDirectory" }
if (Test-Path $Archive) { throw "输出文件已存在: $Archive" }
New-Item -ItemType Directory -Path (Join-Path $OutputDirectory "docs") -Force | Out-Null
Get-ChildItem $ScriptRoot -File | Where-Object { $_.Extension -in @(".sh", ".ps1", ".yaml") -or $_.Name -in @(".env.example", "README.md") } | Copy-Item -Destination $OutputDirectory
Copy-Item (Join-Path $RepoRoot "docs\deployment\ubuntu.md") (Join-Path $OutputDirectory "docs")
Copy-Item (Join-Path $RepoRoot "docs\deployment\windows-docker-desktop.md") (Join-Path $OutputDirectory "docs")
$Checksum = Join-Path $OutputDirectory "SHA256SUMS"
Get-ChildItem $OutputDirectory -Recurse -File | Where-Object { $_.FullName -ne $Checksum } | Sort-Object FullName | ForEach-Object {
    $Relative = $_.FullName.Substring($OutputDirectory.Length + 1).Replace('\', '/')
    "{0}  {1}" -f ((Get-FileHash -Algorithm SHA256 $_.FullName).Hash.ToLowerInvariant()), $Relative
} | Set-Content $Checksum -Encoding ascii
Compress-Archive -Path $OutputDirectory -DestinationPath $Archive
Write-Host "在线发布包: $Archive"
