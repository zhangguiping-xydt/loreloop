param(
    [string]$Version = $(if ($env:LORELOOP_VERSION) { $env:LORELOOP_VERSION } else { "latest" }),
    [switch]$WithWeb,
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
    Write-Host "Downloading LoreLoop Runtime from $ReleaseBase"
    Invoke-WebRequest -Uri "$ReleaseBase/SHA256SUMS" -OutFile $Sums

    $ChecksumLine = Get-Content $Sums | Where-Object {
        $_ -match "^[0-9a-fA-F]{64}\s+(loreloop-[A-Za-z0-9_.+!-]+-py3-none-any\.whl)$"
    } | Select-Object -First 1
    if (-not $ChecksumLine) {
        throw "SHA256SUMS does not contain a valid LoreLoop wheel filename"
    }
    $Parts = $ChecksumLine -split "\s+"
    $Expected = $Parts[0].ToLowerInvariant()
    $WheelName = $Parts[1]
    $Wheel = Join-Path $TempDir $WheelName
    Invoke-WebRequest -Uri "$ReleaseBase/$WheelName" -OutFile $Wheel
    $Actual = (Get-FileHash -Algorithm SHA256 $Wheel).Hash.ToLowerInvariant()
    if ($Actual -ne $Expected) {
        throw "LoreLoop wheel checksum mismatch"
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
        throw "Install uv or pipx, then retry the LoreLoop Runtime installation"
    }

    $Runtime = Get-Command loreloop -ErrorAction SilentlyContinue
    if (-not $Runtime) {
        $Fallback = Join-Path $HOME ".local\bin\loreloop.exe"
        if (Test-Path $Fallback) {
            $RuntimePath = $Fallback
        } else {
            throw "Installation completed but loreloop is not discoverable on PATH"
        }
    } else {
        $RuntimePath = $Runtime.Source
    }

    & $RuntimePath --help | Out-Null
    Write-Host "Installed LoreLoop Runtime: $RuntimePath"

    if ($Init) {
        & $RuntimePath init --skill
    }

    Write-Host 'Next: restart Codex, then invoke $loreloop in your project.'
} finally {
    if (Test-Path $TempDir) {
        Remove-Item -Recurse -Force $TempDir
    }
}
