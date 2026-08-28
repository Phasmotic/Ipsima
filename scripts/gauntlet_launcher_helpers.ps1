Set-StrictMode -Version Latest

function Invoke-TalariaProcess {
    param(
        [Parameter(Mandatory)]
        [string] $FilePath,

        [Parameter(Mandatory)]
        [string[]] $ArgumentList,

        [hashtable] $Environment = @{},

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
    foreach ($entry in $Environment.GetEnumerator()) {
        $startInfo.Environment[$entry.Key] = [string] $entry.Value
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
        Write-Error "GAUNTLET BLOCKED — a required native process could not be executed."
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

function Test-TalariaProcessVersion {
    param(
        [Parameter(Mandatory)]
        [PSCustomObject] $ProcessResult,

        [Parameter(Mandatory)]
        [string] $ExpectedLine
    )

    $versionLines = @(
        $ProcessResult.StandardOutput -split "\r?\n" |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    )
    return (
        $ProcessResult.ExitCode -eq 0 -and
        $ProcessResult.StandardError.Length -eq 0 -and
        $versionLines.Count -ge 1 -and
        $versionLines[0] -ceq $ExpectedLine
    )
}

function ConvertTo-TalariaWslInteropPath {
    param(
        [Parameter(Mandatory)]
        [AllowEmptyString()]
        [string] $WindowsPath
    )

    if (
        [string]::IsNullOrWhiteSpace($WindowsPath) -or
        $WindowsPath -notmatch "^(?<Drive>[A-Za-z]):[\\/]"
    ) {
        return $null
    }
    if ($WindowsPath.IndexOfAny([char[]]@(0, 10, 13)) -ge 0) {
        return $null
    }
    try {
        $fullPath = [System.IO.Path]::GetFullPath($WindowsPath)
    }
    catch {
        return $null
    }
    if ($fullPath -notmatch "^(?<Drive>[A-Za-z]):[\\/](?<Tail>.*)$") {
        return $null
    }
    $drive = $Matches.Drive.ToLowerInvariant()
    $tail = $Matches.Tail.Replace([char] 92, [char] 47)
    if ($tail.Length -eq 0) {
        return "/mnt/$drive"
    }
    return "/mnt/$drive/$tail"
}
