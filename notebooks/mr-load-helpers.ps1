# =============================================================================
# mr-load — shared PowerShell helpers for the Polyglot Notebooks
#   notebooks/mr-load-compass.dib   (setup + explanations)   notebooks/mr-load-run.dib (the sequence)
# Loaded by the Switches cell of each notebook:  Invoke-Expression (Get-Content -Raw <this file>)
# Expects these variables to be set first: $Route ('wsl'|'devcontainer'|'linux'), $Distro, $RepoWsl,
# $RepoWin, $Image, $RepoLinux, $ConfirmLive.  Nothing here stores a secret.
# =============================================================================
$script:LiveSteps = @('bq-load', 'hs-props', 'companies-live', 'attach-upload', 'attach-notes', 'ledger-export', 'deals-live', 'unmigrate')
$env:WSL_UTF8 = '1'   # wsl.exe would otherwise emit UTF-16 into the notebook (g.a.r.b.l.e.d)

function Invoke-Ubuntu {
    <# Runs one bash command line in the repo, inside the Ubuntu of $Route, venv active.
       -Live prepends YES_ALL=1 (skips the runner's YES prompts) and is refused unless $ConfirmLive.
       -Stdin pipes text into the command (used for secrets: never on a command line). #>
    param(
        [Parameter(Mandatory, Position = 0)][string]$Command,
        [switch]$Live,
        [string]$Stdin
    )
    $prefix = ''
    if ($Live) {
        if (-not $ConfirmLive) { Write-Warning "LIVE step refused: set `$ConfirmLive = `$true in the Switches cell first. Not run: $Command"; $global:LASTEXITCODE = 0; return }
        $prefix = 'YES_ALL=1 '
    }
    $venv = '{ [ -f .venv/bin/activate ] && . .venv/bin/activate; }; '
    switch ($Route) {
        'wsl' {
            $exe = 'wsl.exe'
            $nativeArgs = @('-d', $Distro, '-e', 'bash', '-lc', "cd $RepoWsl && $venv$prefix$Command")
        }
        'devcontainer' {
            $exe = 'docker'
            $nativeArgs = @('run', '--rm')
            if ($PSBoundParameters.ContainsKey('Stdin')) { $nativeArgs += '-i' }
            $nativeArgs += @('-v', "${RepoWin}:/work", '-v', 'mrload-gcloud:/root/.config/gcloud', '-w', '/work',
                             '-e', 'MRLOAD_VENV=/opt/venv', $Image, 'bash', '-lc', "$prefix$Command")
        }
        'linux' {
            $exe = 'bash'
            $nativeArgs = @('-lc', "cd $RepoLinux && $venv$prefix$Command")
        }
        default { throw "unknown route '$Route' (wsl | devcontainer | linux)" }
    }
    if ($PSBoundParameters.ContainsKey('Stdin')) { $Stdin | & $exe @nativeArgs } else { & $exe @nativeArgs }
    if ($LASTEXITCODE -ne 0) { Write-Host "✖ exit code $LASTEXITCODE  [$Route]" -ForegroundColor Red }
    else                     { Write-Host "✔ done  [$Route]" -ForegroundColor Green }
}

function Open-UbuntuTerminal {
    <# A real terminal in the route's Ubuntu — for steps that need a keyboard (sudo password, OAuth code). #>
    switch ($Route) {
        'wsl'          { $cmd = 'wsl.exe'; $cargs = @('-d', $Distro, '--cd', '~') }
        'devcontainer' { $cmd = 'docker';  $cargs = @('run', '-it', '--rm', '-v', "${RepoWin}:/work", '-v', 'mrload-gcloud:/root/.config/gcloud', '-w', '/work', $Image, 'bash') }
        default        { Write-Host "route linux: you are already inside Ubuntu — use the VS Code terminal."; return }
    }
    if     (Get-Command wezterm -ErrorAction SilentlyContinue) { Start-Process wezterm -ArgumentList (@('start', '--', $cmd) + $cargs) }
    elseif (Get-Command wt      -ErrorAction SilentlyContinue) { Start-Process wt      -ArgumentList (@($cmd) + $cargs) }
    else                                                        { Start-Process $cmd    -ArgumentList $cargs }
    Write-Host "terminal opened: $cmd $($cargs -join ' ')" -ForegroundColor Cyan
}

function Set-EnvKey {
    <# Masked prompt → value piped into scripts/dev/env_set.sh inside Ubuntu. Nothing is stored in the notebook. #>
    param([Parameter(Mandatory)][string]$Key, [switch]$Plain)
    if ($Plain) { $value = Read-Host "$Key" }
    else {
        $sec   = Read-Host -AsSecureString "$Key (hidden)"
        $value = [System.Net.NetworkCredential]::new('', $sec).Password
    }
    if (-not $value) { Write-Warning "empty value — nothing written"; return }
    Invoke-Ubuntu "scripts/dev/env_set.sh $Key" -Stdin $value
    Remove-Variable value, sec -ErrorAction SilentlyContinue
}

function Show-Route {
    "route        : $Route"
    switch ($Route) {
        'wsl'          { "distro       : $Distro`nrepo (linux) : $RepoWsl" }
        'devcontainer' { "image        : $Image`nrepo (win)   : $RepoWin  → /work" }
        'linux'        { "repo         : $RepoLinux" }
    }
    "ConfirmLive  : $ConfirmLive   (LIVE steps " + $(if ($ConfirmLive) { 'WILL FIRE without prompts' } else { 'are blocked' }) + ")"
}

# ── the sequence ─────────────────────────────────────────────────────────────

function Invoke-Step {
    <# scripts/run_pass1.sh <step>; LIVE steps (gates) go through -Live and are refused unless $ConfirmLive. #>
    param([Parameter(Mandatory, Position = 0)][string]$Step)
    $live = $script:LiveSteps -contains $Step
    Write-Host ("── step {0}{1} ──" -f $Step, $(if ($live) { '  ⟨LIVE⟩' } else { '' })) -ForegroundColor Cyan
    Invoke-Ubuntu "scripts/run_pass1.sh $Step" -Live:$live
}

function Get-PipelineState {
    <# Where am I / what is next — from .mrload/checkpoints.tsv + artefacts (scripts/dev/pipeline_state.sh). -Json returns the object. #>
    param([switch]$Json)
    if (-not $Json) { Invoke-Ubuntu 'scripts/dev/pipeline_state.sh'; return }
    $raw = Invoke-Ubuntu 'scripts/dev/pipeline_state.sh --json' 6>$null
    ($raw -join "`n") | ConvertFrom-Json
}

function Invoke-NextStep {
    <# Runs exactly the next pending step of the sequence (stale predecessors first). #>
    $s = Get-PipelineState -Json
    if (-not $s.next) { Write-Host "nothing pending — $($s.why)" -ForegroundColor Green; return }
    Write-Host "next: $($s.next)   ($($s.why))" -ForegroundColor Cyan
    Invoke-Step $s.next
}

function Invoke-Sequence {
    <# Runs pending steps in order until: the first LIVE step (-UntilLive, or $ConfirmLive is false),
       the step named by -Through has run, a step fails, or nothing is pending. #>
    param([switch]$UntilLive, [string]$Through)
    for ($i = 0; $i -lt 24; $i++) {
        $s = Get-PipelineState -Json
        if (-not $s.next) { Write-Host "nothing pending — $($s.why)" -ForegroundColor Green; return }
        $live = $script:LiveSteps -contains $s.next
        if ($live -and ($UntilLive -or -not $ConfirmLive)) {
            Write-Host "stopped before LIVE step '$($s.next)' — review the dry output above, then set `$ConfirmLive = `$true and run Invoke-Step '$($s.next)' (or Invoke-Sequence again)" -ForegroundColor Yellow
            return
        }
        Invoke-Step $s.next
        if ($LASTEXITCODE -ne 0) { Write-Host "sequence halted at '$($s.next)' (exit $LASTEXITCODE) — fix, then run Invoke-NextStep" -ForegroundColor Red; return }
        if ($Through -and $s.next -eq $Through) { Write-Host "reached '$Through'" -ForegroundColor Green; return }
    }
}
