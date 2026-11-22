<#
.SYNOPSIS
    Set up Stokes on Windows from a GitHub download, and link it to an OpenFOAM engine.

.DESCRIPTION
    Run from the folder you cloned or unzipped (ADR-0046):

        powershell -ExecutionPolicy Bypass -File .\scripts\setup-windows.ps1

    1. Checks Python 3.13/3.14 and Node.js 22.12 or newer.
    2. Creates .venv, installs Stokes with its extras, and builds the front end (web/dist).
    3. Looks for an engine: FoamWin (STOKES_FOAMWIN_ENV or its default folder), or OpenFOAM 14
       in a WSL distribution. Finding one is all the linking there is: Stokes discovers the same
       places on its own.
    4. With no engine, offers to download FoamWin, the native Windows OpenFOAM 14, from its GitHub
       release (OF14-win.zip, checked against the SHA-256 the release publishes) and installs it in
       your user folder. No administrator rights; nothing is downloaded without a yes.
    5. Checks Microsoft MPI (only needed for runs on more than one core).
    6. Writes run-stokes.ps1 and, with -Shortcut, a desktop shortcut to it.

.PARAMETER InstallFoamWin
    Download and install FoamWin without asking when no engine is found.

.PARAMETER NoEngine
    Skip the engine step (CAD, set-up and results work without one).

.PARAMETER Shortcut
    Also create a "Stokes" shortcut on the desktop.

.PARAMETER SkipBuild
    Skip the Python install and the front-end build (engine and launcher steps only).
#>
param(
    [switch]$InstallFoamWin,
    [switch]$NoEngine,
    [switch]$Shortcut,
    [switch]$SkipBuild
)
$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $Root
if (-not (Test-Path (Join-Path $Root 'pyproject.toml'))) {
    throw "Run this from a Stokes checkout of the windows branch (pyproject.toml not found in $Root)."
}

function Step([string]$Text) { Write-Host ""; Write-Host "== $Text" -ForegroundColor Cyan }
function Note([string]$Text) { Write-Host "   $Text" }

# --------------------------------------------------------------------------- 1. tools
Step 'Checking Python and Node.js'
$Python = $null
foreach ($version in @('3.14', '3.13')) {
    try {
        & py "-$version" -c "import sys" 2>$null
        if ($LASTEXITCODE -eq 0) { $Python = @('py', "-$version"); break }
    } catch { }
}
if ($null -eq $Python) {
    throw 'Python 3.13 or 3.14 (64-bit) was not found. Install it from https://www.python.org/downloads/windows/ (tick "Add python.exe to PATH" and the py launcher), then run this script again.'
}
Note "Python: $(& $Python[0] $Python[1] --version)"
if (-not $SkipBuild) {
    $node = Get-Command node -ErrorAction SilentlyContinue
    if ($null -eq $node) {
        throw 'Node.js 22.12 or newer was not found. Install it from https://nodejs.org (the LTS version), then run this script again.'
    }
    $nodeVersion = (& node --version).TrimStart('v')
    if ([version]$nodeVersion -lt [version]'22.12.0') {
        throw "Node.js $nodeVersion is too old; Stokes' front end needs 22.12 or newer."
    }
    Note "Node.js: v$nodeVersion"
}

# --------------------------------------------------------------------------- 2. build
$VenvPython = Join-Path $Root '.venv\Scripts\python.exe'
if (-not $SkipBuild) {
    Step 'Installing Stokes (Python environment, about 1.7 GB)'
    if (-not (Test-Path $VenvPython)) { & $Python[0] $Python[1] -m venv .venv }
    & $VenvPython -m pip install --upgrade pip --quiet
    & $VenvPython -m pip install -e '.[agent,server,ui,cad,extract,explore,materials]' --quiet
    if ($LASTEXITCODE -ne 0) { throw 'pip install failed; see the messages above.' }

    Step 'Building the front end'
    Push-Location (Join-Path $Root 'web')
    try {
        & npm ci --no-audit --no-fund
        if ($LASTEXITCODE -ne 0) { throw 'npm ci failed; see the messages above.' }
        & npm run build
        if ($LASTEXITCODE -ne 0) { throw 'npm run build failed; see the messages above.' }
    } finally { Pop-Location }
} elseif (-not (Test-Path $VenvPython)) {
    throw '-SkipBuild needs an existing .venv; run once without it first.'
}

# --------------------------------------------------------------------------- 3. engine
$Engine = $null
if (-not $NoEngine) {
    Step 'Looking for an OpenFOAM engine'
    $status = & $VenvPython -m stokes.engines.foamwin_install --status | ConvertFrom-Json
    if ($env:STOKES_FOAMWIN_ENV -and (Test-Path $env:STOKES_FOAMWIN_ENV)) {
        $Engine = "FoamWin (STOKES_FOAMWIN_ENV): $env:STOKES_FOAMWIN_ENV"
    } elseif ($status.installed) {
        $Engine = "FoamWin: $($status.installed)"
    } else {
        $wsl = Get-Command wsl.exe -ErrorAction SilentlyContinue
        if ($null -ne $wsl) {
            # **Exit codes decide, not stderr.** Windows PowerShell 5.1 turns a native program's
            # stderr into an error record, and with 'Stop' a harmless WSL warning ("Failed to mount
            # D:\") would end the script; so the probes run with 'Continue'.
            $ErrorActionPreference = 'Continue'
            # `wsl -l -q` writes UTF-16; strip the NULs it leaves behind in a PowerShell string.
            $distros = (& wsl.exe -l -q 2>$null) -replace "`0", '' | Where-Object { $_.Trim() -ne '' }
            foreach ($distro in $distros) {
                & wsl.exe -d $distro.Trim() -- test -f /opt/openfoam14/etc/bashrc 2>$null | Out-Null
                if ($LASTEXITCODE -eq 0) { $Engine = "OpenFOAM 14 in WSL ($($distro.Trim()))"; break }
            }
            $ErrorActionPreference = 'Stop'
        }
    }
    if ($null -ne $Engine) {
        Note "Found $Engine. Stokes uses it with no further setting."
    } else {
        Note 'No OpenFOAM engine found (no FoamWin, and no WSL distribution with OpenFOAM 14).'
        $answer = 'n'
        if ($InstallFoamWin) { $answer = 'y' }
        else {
            Note 'Stokes can download FoamWin, OpenFOAM 14 built for Windows (about 140 MB), from'
            Note 'https://github.com/dhanninacs/foamWin/releases and install it in your user folder.'
            $answer = Read-Host '   Download and install FoamWin now? [y/N]'
        }
        if ($answer -match '^(y|yes)$') {
            & $VenvPython -m stokes.engines.foamwin_install --yes
            if ($LASTEXITCODE -ne 0) { throw 'The FoamWin install did not complete; see the message above.' }
            $Engine = 'FoamWin (just installed)'
        } else {
            Note 'Skipped. Settings > Compute in Stokes offers the same download later.'
        }
    }
    $mpiexec = Join-Path $env:ProgramFiles 'Microsoft MPI\Bin\mpiexec.exe'
    if ($null -ne $Engine -and -not (Test-Path $mpiexec)) {
        Note 'Microsoft MPI is not installed: runs use one core. For parallel runs install msmpisetup.exe'
        Note 'from https://learn.microsoft.com/message-passing-interface/microsoft-mpi'
    }
}

# --------------------------------------------------------------------------- 4. launcher
Step 'Writing the launcher'
$Launcher = Join-Path $Root 'run-stokes.ps1'
@(
    '# Starts Stokes; written by scripts/setup-windows.ps1.',
    'Set-Location $PSScriptRoot',
    '& .\.venv\Scripts\python.exe -m stokes.app @args'
) | Set-Content -Path $Launcher -Encoding utf8
Note "run-stokes.ps1 written in $Root"
if ($Shortcut) {
    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) 'Stokes.lnk'))
    $link.TargetPath = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $link.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Launcher`""
    $link.WorkingDirectory = $Root
    $link.Save()
    Note 'Desktop shortcut "Stokes" created.'
}

Step 'Done'
if ($null -ne $Engine) { Note "Engine: $Engine" } elseif (-not $NoEngine) { Note 'Engine: none yet' }
Note 'Start Stokes with:  powershell -ExecutionPolicy Bypass -File .\run-stokes.ps1'
exit 0
