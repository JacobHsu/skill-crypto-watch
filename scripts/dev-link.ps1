# Link skills/* into .claude/skills/ so Claude Code sessions in this repo load the
# working copy directly: edits take effect in the next session, no plugin reinstall.
# Uses directory junctions, which need no admin rights or Developer Mode.
#   powershell -ExecutionPolicy Bypass -File scripts/dev-link.ps1

$repo = Split-Path -Parent $PSScriptRoot
$target = Join-Path $repo ".claude\skills"
New-Item -ItemType Directory -Force $target | Out-Null

Get-ChildItem (Join-Path $repo "skills") -Directory | Where-Object { Test-Path (Join-Path $_.FullName "SKILL.md") } | ForEach-Object {
    $link = Join-Path $target $_.Name
    if (Test-Path $link) {
        Write-Host "exists: .claude/skills/$($_.Name)"
    } else {
        New-Item -ItemType Junction -Path $link -Target $_.FullName | Out-Null
        Write-Host "linked: .claude/skills/$($_.Name) -> skills/$($_.Name)"
    }
}
