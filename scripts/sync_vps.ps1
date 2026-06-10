[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$VPS_HOST = 'root@213.176.72.32'
$VPS_DIR  = '/opt/news-aggregator'
$VPS_USER = 'news-bot'
$VPS_SVC  = 'news-aggregator'
$LOCAL_DIR = $PSScriptRoot | Split-Path -Parent

function Step([string]$title) {
    Write-Host ''
    Write-Host "=== $title ===" -ForegroundColor Cyan
}
function Ok([string]$msg)   { Write-Host "  [OK]   $msg" -ForegroundColor Green }
function Warn([string]$msg) { Write-Host "  [WARN] $msg" -ForegroundColor Yellow }
function Fail([string]$msg) { Write-Host "  [FAIL] $msg" -ForegroundColor Red }

function Invoke-Ssh([string]$Cmd) {
    $flat = ($Cmd -split "`r?`n" | ForEach-Object { $_.TrimEnd() } | Where-Object { $_ }) -join ' '
    ssh -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new $VPS_HOST $flat
    if ($LASTEXITCODE -ne 0) { throw "ssh exit $LASTEXITCODE" }
}

Step 'Local repo'
Push-Location $LOCAL_DIR
try {
    $branch  = git rev-parse --abbrev-ref HEAD
    git fetch --quiet origin 2>$null
    $head    = git log -1 --oneline
    $status  = git status --porcelain
    $unpushed = git rev-list --count "origin/$branch..HEAD" 2>$null
    if (-not $unpushed) { $unpushed = 0 }
    $hasOrigin = (git remote) -contains 'origin'
    $hasTopic  = Select-String -Path (Join-Path $LOCAL_DIR 'config.toml') -Pattern '\[topics.ai_blogs\]' -Quiet

    Write-Host "  branch: $branch"
    Write-Host "  HEAD:   $head"
    if (-not $hasOrigin) { Fail 'no "origin" remote configured'; throw 'abort' }
    if ($branch -ne 'main') { Warn "local branch is '$branch', not 'main' (VPS pulls origin/main)" }
    if ($status) { Warn 'uncommitted changes:'; $status | ForEach-Object { Write-Host "    $_" } }
    else { Ok 'working tree clean' }
    if ([int]$unpushed -gt 0) { Warn "$unpushed unpushed commit(s) on $branch" }
    else { Ok 'no unpushed commits' }
    if ($hasTopic) { Ok 'local config has [topics.ai_blogs]' }
    else { Warn 'local config.toml missing [topics.ai_blogs]' }
} finally {
    Pop-Location
}

Step 'VPS state (pre)'
$pre = @'
set +e
git config --global --add safe.directory /opt/news-aggregator >/dev/null
cd /opt/news-aggregator
echo "  HEAD: $(git log -1 --oneline)"
git fetch --quiet origin
printf "  behind origin/main: %s\n" "$(git rev-list --count HEAD..origin/main)"
printf "  ahead  origin/main: %s\n" "$(git rev-list --count origin/main..HEAD)"
ws=$(git status --porcelain)
if [ -n "$ws" ]; then printf "  working tree: DIRTY\n%s\n" "$ws" | sed 's/^/    /'; else echo "  working tree: clean"; fi
printf "  service: %s\n" "$(systemctl is-active news-aggregator 2>/dev/null || echo unknown)"
'@
Invoke-Ssh $pre

Step 'Push local to origin'
Push-Location $LOCAL_DIR
try {
    git push origin $branch
    if ($LASTEXITCODE -eq 0) { Ok "pushed to origin/$branch (or already up to date)" }
    else { Fail 'git push failed'; throw 'abort' }
} finally {
    Pop-Location
}

Step 'VPS: stash + pull + reinstall + restart'
$apply = @'
set -e
cd /opt/news-aggregator
sudo -u news-bot -H git stash push -u -m "pre-sync-$(date +%s)" || true
sudo -u news-bot -H git pull
sudo -u news-bot -H .venv/bin/pip install -e . >/dev/null
sudo systemctl restart news-aggregator
sleep 2
'@
Invoke-Ssh $apply

Step 'VPS state (post)'
$post = @'
cd /opt/news-aggregator
echo "  HEAD: $(git log -1 --oneline)"
echo "  topics registered:"
journalctl -u news-aggregator -n 200 --no-pager 2>/dev/null \
  | grep -oE "scheduled [a-z_]+ with cron [^ ]+ [^ ]+ in tz [A-Za-z]+" \
  | sort -u | sed 's/^/    /'
printf "  service: %s\n" "$(systemctl is-active news-aggregator 2>/dev/null || echo unknown)"
echo "  last 20 journal lines:"
journalctl -u news-aggregator -n 20 --no-pager | sed 's/^/    /'
echo "  stashed pre-sync entries:"
sudo -u news-bot -H git stash list | sed 's/^/    /'
'@
Invoke-Ssh $post

Step 'Done'
Write-Host 'A pre-sync-* stash entry (if shown) is either redundant (drop it) or contains a local' -ForegroundColor Yellow
Write-Host 'edit you want to keep (review, then pop or drop):' -ForegroundColor Yellow
Write-Host ''
Write-Host "  ssh $VPS_HOST 'sudo -u $VPS_USER -H git -C $VPS_DIR stash show -p <stash>'"
Write-Host "  ssh $VPS_HOST 'sudo -u $VPS_USER -H git -C $VPS_DIR stash drop'"
