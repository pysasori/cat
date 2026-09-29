$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
    $paths = @(git ls-files data | Where-Object { $_ -match '\.(json|png)$' })
    if ($LASTEXITCODE -ne 0) {
        throw "Could not read the Git file list"
    }
    if ($paths.Count -gt 0) {
        git update-index --no-skip-worktree -- $paths
        if ($LASTEXITCODE -ne 0) {
            throw "Could not restore Git tracking for the local database"
        }
    }
    Write-Host "Database is visible to Git. Run protect-local-db.ps1 after sharing it."
}
finally {
    Pop-Location
}
