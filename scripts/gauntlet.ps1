[CmdletBinding()]
param(
    [switch] $TierB
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "gauntlet_launcher_helpers.ps1")

if ($PSVersionTable.PSVersion.Major -lt 7) {
    Write-Error "GAUNTLET BLOCKED — the launcher requires PowerShell 7 or later."
    exit 1
}

$talariaDistro = "Ubuntu"
$talariaEntry = "powershell-wsl"
$talariaNamespace = "pwsh-wsl-ubuntu-swift-6.3.3"
$talariaRepoWindows = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$talariaWsl = (Get-Command wsl.exe -ErrorAction Stop).Source
$talariaGhLogWsl = ""
$talariaWslEnvironment = @{}

$talariaLauncherSelfTestArguments = @(
    "-NoProfile",
    "-File",
    (Join-Path $PSScriptRoot "test_gauntlet_launcher.ps1")
)
$talariaLauncherSelfTest = Invoke-TalariaProcess `
    -FilePath (Join-Path $PSHOME "pwsh.exe") `
    -ArgumentList $talariaLauncherSelfTestArguments
if (
    $talariaLauncherSelfTest.ExitCode -ne 0 -or
    $talariaLauncherSelfTest.StandardError.Length -ne 0 -or
    $talariaLauncherSelfTest.StandardOutput -cne "GAUNTLET LAUNCHER SELF-TEST PASS"
) {
    Write-Error "GAUNTLET BLOCKED — launcher transport self-tests failed."
    exit 1
}

$talariaRepoWsl = ConvertTo-TalariaWslInteropPath $talariaRepoWindows
if ([string]::IsNullOrWhiteSpace($talariaRepoWsl)) {
    Write-Error "GAUNTLET BLOCKED — the repository path cannot be represented in WSL interop."
    exit 1
}

if ($TierB) {
    try {
        $talariaGhWindows = (
            Get-Command gh.exe -CommandType Application -ErrorAction Stop |
                Select-Object -First 1
        ).Source
    }
    catch {
        Write-Error "GAUNTLET BLOCKED — Tier B requires the Windows GitHub CLI."
        exit 1
    }
    $talariaGhVersion = Invoke-TalariaProcess -FilePath $talariaGhWindows -ArgumentList @(
        "--version"
    )
    if (-not (Test-TalariaProcessVersion `
        -ProcessResult $talariaGhVersion `
        -ExpectedLine "gh version 2.88.1 (2026-03-12)")) {
        Write-Error "GAUNTLET BLOCKED — Tier B requires Windows GitHub CLI 2.88.1."
        exit 1
    }

    $talariaGhLogWsl = ConvertTo-TalariaWslInteropPath $talariaGhWindows
    if ([string]::IsNullOrWhiteSpace($talariaGhLogWsl)) {
        Write-Error "GAUNTLET BLOCKED — the GitHub CLI path could not be translated for WSL."
        exit 1
    }
    $talariaExistingWslEnv = [Environment]::GetEnvironmentVariable("WSLENV", "Process")
    $talariaWslEnvEntries = @(
        $talariaExistingWslEnv -split ":" |
            Where-Object {
                -not [string]::IsNullOrWhiteSpace($_) -and
                $_ -notmatch "^TALARIA_GH_LOG_BIN(?:/.*)?$"
            }
    )
    $talariaWslEnvEntries += "TALARIA_GH_LOG_BIN/up"
    $talariaWslEnvironment = @{
        TALARIA_GH_LOG_BIN = $talariaGhWindows
        WSLENV = $talariaWslEnvEntries -join ":"
    }
}

$talariaScript = "$talariaRepoWsl/scripts/gauntlet.sh"
$talariaArguments = @(
    "-d", $talariaDistro,
    "--", "env",
    "TALARIA_GAUNTLET_ENTRY=$talariaEntry",
    "TALARIA_GAUNTLET_NAMESPACE=$talariaNamespace",
    "bash", $talariaScript
)
if ($TierB) {
    $talariaArguments += "--tier-b"
}

$talariaRun = Invoke-TalariaProcess `
    -FilePath $talariaWsl `
    -ArgumentList $talariaArguments `
    -Environment $talariaWslEnvironment `
    -EchoOutput
if ($talariaRun.ExitCode -eq 0 -and $talariaRun.OutputLength -eq 0) {
    Write-Error "GAUNTLET BLOCKED — WSL returned exit 0 with completely empty output."
    exit 1
}
exit $talariaRun.ExitCode
