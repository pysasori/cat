$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
    $paths = @(git ls-files data | Where-Object { $_ -match '\.(json|png)$' })
    if ($LASTEXITCODE -ne 0) {
        throw "Could not read the Git file list"
    }
    if ($paths.Count -gt 0) {
        git update-index --skip-worktree -- $paths
        if ($LASTEXITCODE -ne 0) {
            throw "Could not protect the local database"
        }
    }
    Write-Host "Local database is hidden from Git. Files remain in place."
}
finally {
    Pop-Location
}
