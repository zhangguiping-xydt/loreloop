param(
    [string]$Version = $(if ($env:LORELOOP_VERSION) { $env:LORELOOP_VERSION } else { "latest" }),
    [switch]$WithWeb,
    [switch]$Codex,
    [switch]$Claude,
    [switch]$OpenCode,
    [switch]$CoMind,
    [switch]$Init
)

$ErrorActionPreference = "Stop"
$Repository = "zhangguiping-xydt/loreloop"

if ($env:LORELOOP_RELEASE_BASE_URL) {
    $ReleaseBase = $env:LORELOOP_RELEASE_BASE_URL.TrimEnd("/")
} elseif ($Version -eq "latest") {
    $ReleaseBase = "https://github.com/$Repository/releases/latest/download"
} else {
    $ReleaseBase = "https://github.com/$Repository/releases/download/$Version"
}

$TempDir = Join-Path ([System.IO.Path]::GetTempPath()) ("loreloop-install-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $TempDir | Out-Null
$Sums = Join-Path $TempDir "SHA256SUMS"

try {
    Write-Host "Downloading LoreLoop from $ReleaseBase"
    Invoke-WebRequest -Uri "$ReleaseBase/SHA256SUMS" -OutFile $Sums

    $ChecksumLine = Get-Content $Sums | Where-Object {
        $_ -match "^[0-9a-fA-F]{64}\s+(loreloop-[A-Za-z0-9_.+!-]+-py3-none-any\.whl)$"
    } | Select-Object -First 1
    if (-not $ChecksumLine) {
        throw "SHA256SUMS does not contain a valid LoreLoop package filename"
    }
    $Parts = $ChecksumLine -split "\s+"
    $Expected = $Parts[0].ToLowerInvariant()
    $WheelName = $Parts[1]
    $Wheel = Join-Path $TempDir $WheelName
    Invoke-WebRequest -Uri "$ReleaseBase/$WheelName" -OutFile $Wheel
    $Actual = (Get-FileHash -Algorithm SHA256 $Wheel).Hash.ToLowerInvariant()
    if ($Actual -ne $Expected) {
        throw "LoreLoop package checksum mismatch"
    }
    Write-Host "Verified SHA-256: $Actual"

    $Spec = $Wheel
    if ($WithWeb) {
        $Spec = "$Wheel[web]"
    }

    $Uv = Get-Command uv -ErrorAction SilentlyContinue
    $Pipx = Get-Command pipx -ErrorAction SilentlyContinue
    if ($Uv) {
        & $Uv.Source tool install --force $Spec
    } elseif ($Pipx) {
        & $Pipx.Source install --force $Spec
    } else {
        throw "Install uv or pipx, then retry LoreLoop installation"
    }

    $LoreLoop = Get-Command loreloop -ErrorAction SilentlyContinue
    if (-not $LoreLoop) {
        $Fallback = Join-Path $HOME ".local\bin\loreloop.exe"
        if (Test-Path $Fallback) {
            $LoreLoopPath = $Fallback
        } else {
            throw "Installation completed but loreloop is not discoverable on PATH"
        }
    } else {
        $LoreLoopPath = $LoreLoop.Source
    }

    & $LoreLoopPath --help | Out-Null
    Write-Host "Installed LoreLoop: $LoreLoopPath"

    if ($Codex) {
        & $LoreLoopPath codex install
    }

    if ($Claude) {
        & $LoreLoopPath claude install
    }

    if ($OpenCode) {
        & $LoreLoopPath opencode install
    }

    if ($CoMind) {
        & $LoreLoopPath comind install
    }

    if ($Init) {
        & $LoreLoopPath init --skill
    }

    if ($Codex -or $Claude -or $OpenCode -or $CoMind) {
        Write-Host "Next: restart the installed coding-agent host, then ask it to use LoreLoop in your project."
    } else {
        Write-Host "Next: run a LoreLoop host integration command or use LoreLoop directly from the terminal."
    }
} finally {
    if (Test-Path $TempDir) {
        Remove-Item -Recurse -Force $TempDir
    }
}
