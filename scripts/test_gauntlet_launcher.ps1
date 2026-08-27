[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "gauntlet_launcher_helpers.ps1")

function Assert-TalariaCondition {
    param(
        [Parameter(Mandatory)]
        [bool] $Condition,

        [Parameter(Mandatory)]
        [string] $Message
    )

    if (-not $Condition) {
        throw $Message
    }
}

function New-TalariaResult {
    param(
        [int] $ExitCode = 0,
        [string] $StandardOutput = "",
        [string] $StandardError = ""
    )

    [PSCustomObject]@{
        ExitCode = $ExitCode
        StandardOutput = $StandardOutput
        StandardError = $StandardError
        OutputLength = $StandardOutput.Length + $StandardError.Length
    }
}

$probeDirectory = $null
$probeFile = $null
try {
    $expectedVersion = "gh version 2.88.1 (2026-03-12)"
    foreach ($lineEnding in @("`n", "`r`n")) {
        $validVersion = New-TalariaResult -StandardOutput "$expectedVersion$lineEnding"
        Assert-TalariaCondition `
            -Condition (Test-TalariaProcessVersion $validVersion $expectedVersion) `
            -Message "a valid version result was rejected"
    }
    $invalidVersions = @(
        (New-TalariaResult -StandardOutput ""),
        (New-TalariaResult -ExitCode 1 -StandardOutput "$expectedVersion`n"),
        (New-TalariaResult -StandardOutput "$expectedVersion`n" -StandardError "warning"),
        (New-TalariaResult -StandardOutput "gh version 0.0.0`n")
    )
    foreach ($invalidVersion in $invalidVersions) {
        Assert-TalariaCondition `
            -Condition (-not (Test-TalariaProcessVersion $invalidVersion $expectedVersion)) `
            -Message "an invalid version result was accepted"
    }

    $validPath = ConvertTo-TalariaWslInteropPath `
        -WindowsPath "C:\Program Files\GitHub CLI\gh.exe"
    Assert-TalariaCondition `
        -Condition ($validPath -ceq "/mnt/c/Program Files/GitHub CLI/gh.exe") `
        -Message "a valid CRLF interop path was rejected"
    $invalidPaths = @("", "relative\gh.exe", "\\server\share\gh.exe", "/usr/bin/gh", "C:\bad`npath")
    foreach ($invalidPath in $invalidPaths) {
        Assert-TalariaCondition `
            -Condition ([string]::IsNullOrEmpty((ConvertTo-TalariaWslInteropPath $invalidPath))) `
            -Message "an invalid or ambiguous interop path was accepted"
    }

    $artifactRoot = Join-Path (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")) ".gauntlet"
    [void] (New-Item -ItemType Directory -Force -Path $artifactRoot)
    $probeDirectory = Join-Path $artifactRoot ("launcher-selftest-" + [guid]::NewGuid().ToString("N"))
    [void] (New-Item -ItemType Directory -Path $probeDirectory)
    $probeFile = Join-Path $probeDirectory "record arguments.ps1"
    [System.IO.File]::WriteAllText(
        $probeFile,
        '[Console]::Out.Write((@{ Arguments = @($args); Environment = $env:TALARIA_GH_LOG_BIN } | ConvertTo-Json -Compress))',
        [System.Text.UTF8Encoding]::new($false)
    )
    $expectedArguments = @(
        "-d",
        "Ubuntu",
        "--",
        "bash",
        "/mnt/c/repository path/scripts/gauntlet.sh",
        "--tier-b"
    )
    $probeArguments = @("-NoProfile", "-File", $probeFile) + $expectedArguments
    $probeResult = Invoke-TalariaProcess `
        -FilePath (Join-Path $PSHOME "pwsh.exe") `
        -ArgumentList $probeArguments `
        -Environment @{
            TALARIA_GH_LOG_BIN = "/mnt/c/Program Files/GitHub CLI/gh.exe"
        }
    Assert-TalariaCondition ($probeResult.ExitCode -eq 0) "the argument probe failed"
    Assert-TalariaCondition ($probeResult.StandardError.Length -eq 0) "the argument probe wrote stderr"
    Assert-TalariaCondition ($probeResult.StandardOutput.Length -gt 0) "the argument probe returned empty output"
    $probeEvidence = $probeResult.StandardOutput | ConvertFrom-Json
    $actualArguments = @($probeEvidence.Arguments)
    Assert-TalariaCondition `
        -Condition ($probeEvidence.Environment -ceq "/mnt/c/Program Files/GitHub CLI/gh.exe") `
        -Message "the launcher changed the environment value"
    Assert-TalariaCondition `
        -Condition ($actualArguments.Count -eq $expectedArguments.Count) `
        -Message "the launcher changed the argument count"
    for ($index = 0; $index -lt $expectedArguments.Count; $index++) {
        Assert-TalariaCondition `
            -Condition ($actualArguments[$index] -ceq $expectedArguments[$index]) `
            -Message "the launcher changed an argument boundary"
    }

    [Console]::Out.Write("GAUNTLET LAUNCHER SELF-TEST PASS")
}
catch {
    [Console]::Error.Write("GAUNTLET LAUNCHER SELF-TEST BLOCKED")
    exit 1
}
finally {
    if ($null -ne $probeFile -and (Test-Path -LiteralPath $probeFile -PathType Leaf)) {
        Remove-Item -LiteralPath $probeFile -Force
    }
    if ($null -ne $probeDirectory -and (Test-Path -LiteralPath $probeDirectory -PathType Container)) {
        Remove-Item -LiteralPath $probeDirectory -Force
    }
}
