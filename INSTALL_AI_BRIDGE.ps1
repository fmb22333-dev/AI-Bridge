[CmdletBinding()]
param(
    [string]$InstallDir = "",
    [string]$Ref = "main",
    [switch]$NoLaunch
)

$ErrorActionPreference = "Stop"
$ProductRepository = "fmb22333-dev/AI-Bridge"

if ([string]::IsNullOrWhiteSpace($InstallDir)) {
    $base = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { $HOME }
    $InstallDir = Join-Path $base "AI_Bridge"
}
$InstallDir = [System.IO.Path]::GetFullPath($InstallDir)

function Get-ProductToken {
    foreach ($name in @("AI_BRIDGE_PRODUCT_TOKEN", "GH_TOKEN", "GITHUB_TOKEN")) {
        $value = [Environment]::GetEnvironmentVariable($name)
        if (-not [string]::IsNullOrWhiteSpace($value)) {
            return $value.Trim()
        }
    }
    $gh = Get-Command gh -ErrorAction SilentlyContinue
    if ($gh) {
        try {
            $value = (& gh auth token 2>$null | Select-Object -First 1)
            if (-not [string]::IsNullOrWhiteSpace($value)) {
                return $value.Trim()
            }
        } catch {}
    }
    return ""
}

$Token = Get-ProductToken
$Headers = @{
    "Accept" = "application/vnd.github+json"
    "User-Agent" = "AI-Bridge-Installer"
    "X-GitHub-Api-Version" = "2022-11-28"
}
if ($Token) {
    $Headers["Authorization"] = "Bearer $Token"
}

function Get-RepoBytes([string]$Path) {
    $escaped = ($Path -split "/" | ForEach-Object { [uri]::EscapeDataString($_) }) -join "/"
    $encodedRef = [uri]::EscapeDataString($Ref)
    $url = "https://api.github.com/repos/${ProductRepository}/contents/${escaped}?ref=${encodedRef}"
    $lastError = $null
    for ($attempt = 1; $attempt -le 6; $attempt++) {
        try {
            $response = Invoke-RestMethod -Uri $url -Headers $Headers -Method Get
            if (-not $response.content) {
                throw "GitHub returned no content for '$Path'."
            }
            return [Convert]::FromBase64String(($response.content -replace "\s", ""))
        } catch {
            $lastError = $_
            if ($attempt -lt 6) {
                Start-Sleep -Seconds ([Math]::Min(5, $attempt))
            }
        }
    }
    throw "Unable to download '$Path' from $ProductRepository@$Ref after bounded retries. If the product repository is private, sign in with GitHub CLI or set AI_BRIDGE_PRODUCT_TOKEN/GH_TOKEN. $($lastError.Exception.Message)"
}

function Get-RepoJson([string]$Path) {
    $bytes = Get-RepoBytes $Path
    return ([Text.Encoding]::UTF8.GetString($bytes) | ConvertFrom-Json)
}

function Get-Sha256Hex([byte[]]$Bytes) {
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        return ([BitConverter]::ToString($sha.ComputeHash($Bytes))).Replace("-", "").ToLowerInvariant()
    }
    finally {
        $sha.Dispose()
    }
}

function Write-RepoFile([string]$SourcePath, [string]$TargetPath, [string]$ExpectedSha256 = "") {
    $bytes = Get-RepoBytes $SourcePath
    if ($ExpectedSha256) {
        $actual = Get-Sha256Hex $bytes
        if ($actual -ne $ExpectedSha256.ToLowerInvariant()) {
            throw "SHA-256 mismatch for $SourcePath"
        }
    }
    $parent = Split-Path -Parent $TargetPath
    if ($parent) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }
    [IO.File]::WriteAllBytes($TargetPath, $bytes)
}

Write-Host "[AI Bridge] Reading product release authority..."
$Release = Get-RepoJson "release-manifest.json"
$Runtime = Get-RepoJson ([string]$Release.runtime_manifest)
$Supervisor = Get-RepoJson ([string]$Release.supervisor_manifest)

$Temp = Join-Path ([IO.Path]::GetTempPath()) ("ai_bridge_install_" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $Temp | Out-Null

try {
    $bundle = Join-Path $Temp "runtime_bundle.zip"
    Write-RepoFile ([string]$Runtime.bundle_path) $bundle ([string]$Runtime.bundle_sha256)

    $extract = Join-Path $Temp "extract"
    Expand-Archive -LiteralPath $bundle -DestinationPath $extract -Force
    $payload = Join-Path $extract ([string]$Runtime.runtime_path)
    if (-not (Test-Path (Join-Path $payload "pyproject.toml"))) {
        throw "Runtime payload is invalid: pyproject.toml is missing."
    }

    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null

    $systemPython = Join-Path $InstallDir "_System\.venv\Scripts\python.exe"
    $oldSupervisor = Join-Path $InstallDir "_System\supervisor.py"
    if ((Test-Path $systemPython) -and (Test-Path $oldSupervisor)) {
        try {
            & $systemPython $oldSupervisor stop | Out-Null
        } catch {}
        Start-Sleep -Milliseconds 500
    }

    $runtimeRoot = Join-Path $InstallDir "Runtime"
    $current = Join-Path $runtimeRoot "Current"
    $previous = Join-Path $runtimeRoot "Previous"
    $staged = Join-Path $runtimeRoot ".install-current"
    New-Item -ItemType Directory -Force -Path $runtimeRoot | Out-Null

    if (Test-Path $staged) {
        Remove-Item -Recurse -Force $staged
    }
    Copy-Item -Recurse -Force $payload $staged

    if (Test-Path $previous) {
        Remove-Item -Recurse -Force $previous
    }
    if (Test-Path $current) {
        Move-Item -Force $current $previous
    }
    Move-Item -Force $staged $current

    foreach ($item in @($Supervisor.install_files)) {
        $target = Join-Path $InstallDir ([string]$item.target)
        Write-RepoFile ([string]$item.source_path) $target ([string]$item.sha256)
    }

    $dataDir = if ($env:AI_BRIDGE_DATA_DIR) { $env:AI_BRIDGE_DATA_DIR } else { Join-Path $HOME ".ai_bridge" }
    New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
    $updateSource = @{
        repository = $ProductRepository
        branch = "main"
        manifest_path = "runtime-release.json"
        auto_update = $true
        check_interval_seconds = 30
        bootstrap_from_bus = $false
    } | ConvertTo-Json -Depth 4
    $utf8NoBom = New-Object Text.UTF8Encoding($false)
    [IO.File]::WriteAllText(
        (Join-Path $dataDir "update_source.json"),
        $updateSource + [Environment]::NewLine,
        $utf8NoBom
    )

    Write-Host "[AI Bridge] Installed Runtime $($Runtime.version), Supervisor $($Supervisor.version)"
    Write-Host "[AI Bridge] Location: $InstallDir"
    Write-Host "[AI Bridge] Existing user project/Bus state was not imported or overwritten."

    if (-not $NoLaunch) {
        Start-Process -FilePath (Join-Path $InstallDir "AI_Bridge.bat") -WorkingDirectory $InstallDir
    }
}
finally {
    if (Test-Path $Temp) {
        Remove-Item -Recurse -Force $Temp
    }
}
