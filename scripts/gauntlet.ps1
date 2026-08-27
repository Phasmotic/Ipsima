[CmdletBinding()]
param(
    [switch] $TierB
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($PSVersionTable.PSVersion.Major -lt 7) {
    Write-Error "GAUNTLET BLOCKED — the launcher requires PowerShell 7 or later."
    exit 1
}

function Invoke-TalariaProcess {
    param(
        [Parameter(Mandatory)]
        [string] $FilePath,

        [Parameter(Mandatory)]
        [string[]] $ArgumentList,

        [switch] $EchoOutput
    )

    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $FilePath
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $startInfo.StandardOutputEncoding = [System.Text.UTF8Encoding]::new($false)
    $startInfo.StandardErrorEncoding = [System.Text.UTF8Encoding]::new($false)
    foreach ($argument in $ArgumentList) {
        [void] $startInfo.ArgumentList.Add($argument)
    }

    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo = $startInfo
    try {
        if (-not $process.Start()) {
            throw "The native process did not start."
        }

        # Drain both streams concurrently so a full pipe cannot deadlock the launcher.
        $standardOutputTask = $process.StandardOutput.ReadToEndAsync()
        $standardErrorTask = $process.StandardError.ReadToEndAsync()
        $process.WaitForExit()
        $standardOutput = $standardOutputTask.GetAwaiter().GetResult()
        $standardError = $standardErrorTask.GetAwaiter().GetResult()
        $exitCode = $process.ExitCode
    }
    catch {
        Write-Error "GAUNTLET BLOCKED — the WSL process could not be executed."
        exit 1
    }
    finally {
        $process.Dispose()
    }

    if ($EchoOutput) {
        if ($standardOutput.Length -gt 0) {
            [Console]::Out.Write($standardOutput)
        }
        if ($standardError.Length -gt 0) {
            [Console]::Error.Write($standardError)
        }
    }

    [PSCustomObject]@{
        ExitCode = $exitCode
        StandardOutput = $standardOutput
        StandardError = $standardError
        OutputLength = $standardOutput.Length + $standardError.Length
    }
}

$talariaDistro = "Ubuntu"
$talariaEntry = "powershell-wsl"
$talariaNamespace = "pwsh-wsl-ubuntu-swift-6.3.3"
$talariaRepoWindows = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$talariaRepoPortable = $talariaRepoWindows.Replace([char] 92, [char] 47)
$talariaWsl = (Get-Command wsl.exe -ErrorAction Stop).Source

$talariaTranslation = Invoke-TalariaProcess -FilePath $talariaWsl -ArgumentList @(
    "-d", $talariaDistro,
    "--", "wslpath", "-a", "-u", $talariaRepoPortable
)
$talariaRepoWsl = $talariaTranslation.StandardOutput -split "\r?\n" |
    Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
    Select-Object -Last 1

if ($talariaTranslation.ExitCode -ne 0 -or [string]::IsNullOrWhiteSpace($talariaRepoWsl)) {
    Write-Error "GAUNTLET BLOCKED — WSL path translation returned no usable result."
    exit 1
}
$talariaRepoWsl = $talariaRepoWsl.Trim()
if (-not $talariaRepoWsl.StartsWith("/", [StringComparison]::Ordinal)) {
    Write-Error "GAUNTLET BLOCKED — WSL path translation returned an invalid result."
    exit 1
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

$talariaRun = Invoke-TalariaProcess -FilePath $talariaWsl -ArgumentList $talariaArguments -EchoOutput
if ($talariaRun.ExitCode -eq 0 -and $talariaRun.OutputLength -eq 0) {
    Write-Error "GAUNTLET BLOCKED — WSL returned exit 0 with completely empty output."
    exit 1
}
exit $talariaRun.ExitCode
