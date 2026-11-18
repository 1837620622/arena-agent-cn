# arena-agent installer for Windows.
#
#   powershell -ExecutionPolicy Bypass -File .\install.ps1
#
# Installs uv (if missing), then arena-agent with its own private Python (your Python, if any, is not touched),
# registers the MCP server and the arena-modeling skill with Claude Code, Claude Desktop and Codex (whichever are
# installed), and runs a health check. Run it again to update.
#
# Options (environment variables, set before running):
#   ARENA_AGENT_SOURCE   what to install (default: the latest code on GitHub; a local folder works for development)
#   ARENA_AGENT_CLIENTS  space-separated clients to register (default: all found): claude-code claude-desktop codex

param([switch]$UpdateOnly)

# Not 'Stop': Windows PowerShell 5.1 turns anything a program writes to stderr (uv prints progress and notes there)
# into a terminating error. Failures are checked explicitly with $LASTEXITCODE instead.
$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'

function Say($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }

if ($env:OS -ne 'Windows_NT') { throw 'arena-agent needs Windows (Rockwell Arena runs only on Windows).' }

$src = $env:ARENA_AGENT_SOURCE
if (-not $src) { $src = 'https://github.com/1837620622/arena-agent-cn/archive/refs/heads/main.zip' }

# 1. uv: a small tool that installs Python apps in their own environment
$uv = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uv) {
    Say 'Installing uv (Python app installer from astral.sh)'
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
    $uv = Get-Command uv -ErrorAction SilentlyContinue
    if (-not $uv) { throw 'uv could not be installed (see the messages above).' }
}

# 2. arena-agent itself, with a private Python 3.12
$tools = & $uv.Source tool dir
if ($LASTEXITCODE -ne 0 -or -not $tools) { throw 'uv could not locate its tool directory.' }
$bin = & $uv.Source tool dir --bin
if ($LASTEXITCODE -ne 0 -or -not $bin) { throw 'uv could not locate its command directory.' }
$scripts = Join-Path $tools.Trim() 'arena-agent\Scripts\'
$exe = Join-Path $bin.Trim() 'arena-agent.exe'

# A running Windows tool can hold its interpreter open while uv replaces the environment.
if (Test-Path -LiteralPath $scripts) {
    $running = @(Get-CimInstance -ClassName Win32_Process -ErrorAction Stop `
        -Filter "Name='python.exe' OR Name='pythonw.exe' OR Name='arena-agent.exe'" | Where-Object {
            $_.ExecutablePath -and (
                $_.ExecutablePath.StartsWith($scripts, [StringComparison]::OrdinalIgnoreCase) -or
                $_.ExecutablePath -ieq $exe
            )
        })
    if ($running.Count) {
        throw ('arena-agent is in use (PID ' + ($running.ProcessId -join ', ') +
               '). Close the MCP session or CLI job using it, then retry the update.')
    }
}

Say "Installing arena-agent from $src"
& $uv.Source tool install --force --refresh-package arena-agent --python 3.12 $src
if ($LASTEXITCODE -ne 0) { throw 'uv could not install arena-agent (see the messages above).' }

if ($UpdateOnly) {
    & $exe --version
    if ($LASTEXITCODE -ne 0) { throw 'The updated arena-agent could not start.' }
    return
}

& $uv.Source tool update-shell *> $null  # puts uv's tool folder on PATH for new terminals

# 3. register with the AI clients on this machine
Say 'Connecting arena-agent to your AI tools'
if ($env:ARENA_AGENT_CLIENTS) {
    $clientList = @($env:ARENA_AGENT_CLIENTS.Split(' ') | Where-Object { $_ })
    & $exe install --client $clientList
} else {
    & $exe install
}
if ($LASTEXITCODE -ne 0) { throw 'arena-agent could not register itself with your AI tools (see the messages above).' }

# 4. health check (starts Arena once; allow up to a minute)
Say 'Checking Arena (this starts Arena once; allow up to a minute)'
& $exe doctor
if ($LASTEXITCODE -eq 0) {
    Say 'Done. Start a new Claude / Codex session and ask it to model something in Arena.'
} else {
    Write-Host ''
    Write-Host 'arena-agent is installed, but some checks failed: follow the "fix" lines above, then run: arena-agent doctor' -ForegroundColor Yellow
}
