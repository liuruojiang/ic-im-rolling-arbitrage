$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
Push-Location -LiteralPath $projectRoot
try {
    git diff --cached --quiet
    if ($LASTEXITCODE -ne 0) { throw 'Existing staged changes found. Review them before running this script.' }
    $expected = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'preaudit_commit_hashes.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach ($item in $expected.PSObject.Properties) {
        $actual = (Get-FileHash -LiteralPath (Join-Path $projectRoot $item.Name) -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actual -ne $item.Value) { throw ('File changed after preparation: ' + $item.Name) }
    }
    $pathsFile = Join-Path $PSScriptRoot 'preaudit_paths.nul'
    git -c core.autocrlf=false add --pathspec-from-file=$pathsFile --pathspec-file-nul
    if ($LASTEXITCODE -ne 0) { throw 'git add failed; no commit made.' }
    git -c core.autocrlf=false commit -m "chore: checkpoint cumulative IC IM v1.4 fixes and audit evidence before red-team review"
    if ($LASTEXITCODE -ne 0) { throw 'git commit failed; inspect the staged files.' }
    git log -1 --format=fuller
} finally {
    Pop-Location
}
