[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-f]{40}$')]
    [string]$ExpectedCommit
)

$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
Set-Location -LiteralPath $projectRoot

function Invoke-CheckedGit {
    param([string[]]$Arguments)
    $result = & git @Arguments
    if ($LASTEXITCODE -ne 0) { throw ('Git failed: ' + ($Arguments -join ' ')) }
    return $result
}

$branch = Invoke-CheckedGit -Arguments @('branch', '--show-current')
if ($branch -ne 'main') { throw 'Expected the original main branch; preserve the current branch.' }
$remote = Invoke-CheckedGit -Arguments @('remote', 'get-url', 'origin')
if ($remote -notin @('https://github.com/liuruojiang/ic-im-rolling-arbitrage.git',
                     'git@github.com:liuruojiang/ic-im-rolling-arbitrage.git')) {
    throw 'This is not the reviewed strategy repository.'
}
& git diff --cached --quiet
if ($LASTEXITCODE -ne 0) { throw 'The index has staged changes; keep them for separate review.' }

Invoke-CheckedGit -Arguments @('fetch', 'origin', 'main') | Out-Host
$fetched = Invoke-CheckedGit -Arguments @('rev-parse', 'refs/remotes/origin/main')
if ($fetched -ne $ExpectedCommit) { throw 'Remote main moved; review the new commit before synchronizing.' }
& git merge-base --is-ancestor HEAD $ExpectedCommit
if ($LASTEXITCODE -ne 0) { throw 'Local history diverged; preserve it for separate review.' }

$changed = @(Invoke-CheckedGit -Arguments @('-c', 'core.quotepath=false', 'diff', '--name-only', '--no-renames', $ExpectedCommit, '--'))
$prefix = $projectRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
foreach ($relative in $changed) {
    if ([string]::IsNullOrWhiteSpace($relative)) { continue }
    $full = [IO.Path]::GetFullPath((Join-Path $projectRoot $relative))
    if (-not $full.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'A comparison path escaped the reviewed project.'
    }
    if (-not (Test-Path -LiteralPath $full -PathType Leaf)) {
        throw ('A reviewed file is missing: ' + $relative)
    }
    $actual = Invoke-CheckedGit -Arguments @('hash-object', '--no-filters', '--', $relative)
    $expected = Invoke-CheckedGit -Arguments @('rev-parse', ($ExpectedCommit + ':' + $relative))
    if ($actual -ne $expected) { throw ('Working bytes differ from the published commit: ' + $relative) }
}

# All changed working files already equal the fetched immutable commit.
# Update only HEAD/index; keep working files, local evidence and runtime data.
Invoke-CheckedGit -Arguments @('reset', '--mixed', $ExpectedCommit) | Out-Host
$remaining = @(Invoke-CheckedGit -Arguments @('status', '--porcelain=v1', '--untracked-files=all'))
if ($remaining.Count -ne 0) {
    $remaining | Out-Host
    throw 'Additional local work remains; it has been preserved.'
}
Write-Output ('Synchronized ' + $ExpectedCommit + '; working tree is clean.')
