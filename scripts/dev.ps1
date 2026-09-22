#Requires -Version 5.1
<#
.SYNOPSIS
    Bring the Migration Agent stack up, either via Docker or locally, and keep
    it in sync with .env changes.

.DESCRIPTION
    One entry point for "get this running" instead of memorizing the docker
    compose / venv / npm commands from the README separately.

    - Never overwrites an existing .env or frontend/.env.local - only creates
      them from the *.example templates the first time they're missing, and
      generates a real JWT_SECRET for a freshly-created .env.
    - Idempotent: running it again with nothing changed just confirms the
      stack is up.
    - Picks up edits to .env: -Reload force-recreates every container (Docker
      mode) or kills and restarts the tracked local processes (local mode) so
      new environment variables actually take effect. Without -Reload, a
      plain re-run still rebuilds/relinks anything whose code changed
      (Docker's own image-diff recreate, or a fresh pip/npm install locally)
      but won't disturb an already-running process just because .env's
      contents differ - Docker in particular can't detect that on its own.

.PARAMETER Mode
    'docker' (default) - db, redis, api, web all run in containers via
    docker-compose.yml. 'local' - db/redis still run via Docker (nothing here
    installs a local Postgres/Redis), but the API runs via "python run.py"
    and the frontend via "npm run dev" as tracked background processes.

.PARAMETER Reload
    Force everything to pick up the current .env / frontend/.env.local
    contents, even if nothing looks different to Docker or to this script.
    Use this after editing .env.

.PARAMETER Stop
    Tear down whatever this script started (docker compose down, or kill the
    tracked local backend/frontend processes). Leaves .env, node_modules, and
    the Python venv alone.

.EXAMPLE
    .\scripts\dev.ps1
    First run: creates .env if missing, builds and starts the full Docker stack.

.EXAMPLE
    .\scripts\dev.ps1 -Reload
    You just edited .env (e.g. dropped in a real ANTHROPIC_API_KEY) - force
    every container to restart with the new values.

.EXAMPLE
    .\scripts\dev.ps1 -Mode local -Reload
    Restart the locally-run backend/frontend processes (db/redis stay in
    Docker) to pick up a .env change.

.EXAMPLE
    .\scripts\dev.ps1 -Stop
    Stop the Docker stack (or the locally-run processes, with -Mode local).
#>
[CmdletBinding()]
param(
    [ValidateSet('docker', 'local')]
    [string]$Mode = 'docker',

    [switch]$Reload,
    [switch]$Stop
)

# Deliberately NOT setting $ErrorActionPreference = 'Stop': this script shells
# out to docker/npm/pip/alembic, all of which write ordinary progress info to
# stderr. PowerShell 5.1 wraps a native command's stderr lines as ErrorRecords,
# and with $ErrorActionPreference = 'Stop' any one of those aborts the whole
# script even though the command itself succeeded. Every native call below is
# guarded by its own $LASTEXITCODE check instead.
$RepoRoot = Split-Path -Parent $PSScriptRoot
$LocalRunDir = Join-Path $RepoRoot '.local_run'

function Write-Step {
    param([string]$Message)
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Write-Warn2 {
    param([string]$Message)
    Write-Host "!!  $Message" -ForegroundColor Yellow
}

function New-JwtSecret {
    $bytes = New-Object byte[] 32
    [System.Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
    ([Convert]::ToBase64String($bytes)).TrimEnd('=').Replace('+', '-').Replace('/', '_')
}

function Confirm-EnvFile {
    # Never overwrites an existing .env - that's the whole point of the
    # "should work even when files like env are updated" requirement: this
    # script must be safe to re-run without clobbering secrets someone
    # already filled in.
    $envPath = Join-Path $RepoRoot '.env'
    $envExamplePath = Join-Path $RepoRoot '.env.example'
    if (-not (Test-Path $envPath)) {
        Write-Step "No .env found - creating one from .env.example"
        Copy-Item $envExamplePath $envPath
        $secret = New-JwtSecret
        (Get-Content $envPath -Raw) -replace 'JWT_SECRET=changeme-generate-a-real-secret', "JWT_SECRET=$secret" |
            Set-Content $envPath -NoNewline
        Write-Warn2 "Generated a real JWT_SECRET. ANTHROPIC_API_KEY is still a placeholder - edit .env before real LLM calls will work."
    }

    $frontendEnvPath = Join-Path $RepoRoot 'frontend\.env.local'
    $frontendEnvExamplePath = Join-Path $RepoRoot 'frontend\.env.local.example'
    if (-not (Test-Path $frontendEnvPath)) {
        Write-Step "No frontend/.env.local found - creating one from .env.local.example"
        Copy-Item $frontendEnvExamplePath $frontendEnvPath
    }

    Confirm-DevpConfig
}

function Confirm-DevpConfig {
    # .devprune.json is the PERSONAL dev-prune config (per machine/clone) -
    # distinct from the committed project.devprune.json already in the repo.
    # devp writes it itself (and registers it in .git/info/exclude, never
    # .gitignore) so its $schema pointer and scaffolding stay correct - this
    # script never hand-writes the JSON. Purely a convenience for anyone who
    # has dev-prune installed; silently skipped otherwise, since it has
    # nothing to do with actually running the app.
    $devpCmd = Get-Command devp -ErrorAction SilentlyContinue
    if (-not $devpCmd) {
        $devpCmd = Get-Command dev-prune -ErrorAction SilentlyContinue
    }
    if (-not $devpCmd) {
        return
    }

    $devprunePersonalPath = Join-Path $RepoRoot '.devprune.json'
    if (-not (Test-Path $devprunePersonalPath)) {
        Write-Step "dev-prune found - creating this machine's .devprune.json"
        Push-Location $RepoRoot
        & $devpCmd.Source config project . | Out-Null
        Pop-Location
    }
}

function Wait-ForHealth {
    param([string]$Url = 'http://localhost:8000/health/ready', [int]$TimeoutSeconds = 60)
    Write-Step "Waiting for $Url to report ready (up to ${TimeoutSeconds}s)"
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $resp = Invoke-RestMethod -Uri $Url -TimeoutSec 3
            if ($resp.status -eq 'ready') {
                Write-Host "    ready: $($resp | ConvertTo-Json -Compress)" -ForegroundColor Green
                return $true
            }
        } catch {
            # not up yet - keep polling
        }
        Start-Sleep -Seconds 2
    }
    Write-Warn2 "Timed out waiting for $Url - check logs (docker compose logs api, or .local_run\backend.log)"
    return $false
}

function Get-EnvValues {
    # Reads .env once and returns every KEY=value line as a hashtable, so
    # callers needing several values (bootstrap credentials, the local-mode
    # DB/Redis connection pieces below) don't each re-open and re-scan the
    # same short file.
    $envPath = Join-Path $RepoRoot '.env'
    $values = @{}
    foreach ($line in Get-Content $envPath) {
        if ($line -match '^([A-Za-z_][A-Za-z0-9_]*)=(.*)$') {
            $values[$Matches[1]] = $Matches[2]
        }
    }
    $values
}

function Get-BootstrapCredentials {
    $env = Get-EnvValues
    [PSCustomObject]@{ Email = $env['BOOTSTRAP_ADMIN_EMAIL']; Password = $env['BOOTSTRAP_ADMIN_PASSWORD'] }
}

function Get-LocalConnectionEnv {
    # docker-compose.yml overrides DATABASE_URL/REDIS_URL for the api
    # container to point at the db/redis service names, which only resolve
    # inside the Docker network. .env's own values are those same Docker
    # hostnames (correct for that override, never actually read by the
    # container itself) - a python run.py started directly on the host needs
    # the equivalent localhost + published-port form instead, or it can
    # never resolve "db"/"redis" at all. Mirrors docker-compose.yml's own
    # user/password/db defaults.
    $env = Get-EnvValues
    $pgUser = if ($env['POSTGRES_USER']) { $env['POSTGRES_USER'] } else { 'migration' }
    $pgPassword = if ($env['POSTGRES_PASSWORD']) { $env['POSTGRES_PASSWORD'] } else { 'migration' }
    $pgDb = if ($env['POSTGRES_DB']) { $env['POSTGRES_DB'] } else { 'migration_agent' }
    $pgPort = if ($env['POSTGRES_HOST_PORT']) { $env['POSTGRES_HOST_PORT'] } else { '5432' }
    $redisPort = if ($env['REDIS_HOST_PORT']) { $env['REDIS_HOST_PORT'] } else { '6379' }
    [PSCustomObject]@{
        DatabaseUrl = "postgresql+psycopg://${pgUser}:${pgPassword}@localhost:${pgPort}/${pgDb}"
        RedisUrl    = "redis://localhost:${redisPort}/0"
    }
}

function Invoke-DockerMode {
    Push-Location $RepoRoot
    try {
        if ($Stop) {
            Write-Step "Stopping the Docker stack"
            docker compose down
            return
        }

        Confirm-EnvFile

        if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
            throw "docker is not on PATH. Install Docker Desktop, or use -Mode local."
        }

        $upArgs = @('compose', 'up', '--build', '-d')
        if ($Reload) {
            # Force-recreate is the mechanism that actually re-reads .env - a
            # plain "up -d" only recreates a container whose image changed,
            # and .env's env_file reference never looks different to Docker
            # even when its contents do.
            Write-Step "Reload requested - forcing every container to recreate with the current .env"
            $upArgs += '--force-recreate'
        } else {
            Write-Step "Bringing the Docker stack up (build only what changed)"
        }
        & docker @upArgs
        if ($LASTEXITCODE -ne 0) { throw "docker compose up failed (exit $LASTEXITCODE)" }

        if (Wait-ForHealth) {
            $creds = Get-BootstrapCredentials
            Write-Host ""
            Write-Host "Web:   http://localhost:3000" -ForegroundColor Green
            Write-Host "API:   http://localhost:8000/docs" -ForegroundColor Green
            Write-Host "Login: $($creds.Email) / $($creds.Password)" -ForegroundColor Green
        }
    } finally {
        Pop-Location
    }
}

function Stop-TrackedProcess {
    param([string]$Name, [string]$PidFile)
    if (Test-Path $PidFile) {
        $procId = Get-Content $PidFile -Raw
        Stop-Process -Id ([int]$procId) -Force -ErrorAction SilentlyContinue
        Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
        Write-Step "Stopped $Name (pid $procId)"
    }
}

function Test-TrackedProcessAlive {
    # A crashed process (as opposed to one this script stopped) never gets its
    # PID file cleaned up by Stop-TrackedProcess, since nothing calls that on
    # a crash. Without this check, a stale file from a dead process makes a
    # plain re-run believe it's "already running" forever and never restart
    # it. Cleans up the stale file itself so the caller can just start fresh.
    param([string]$PidFile)
    if (-not (Test-Path $PidFile)) {
        return $false
    }
    $procId = Get-Content $PidFile -Raw
    if (Get-Process -Id ([int]$procId) -ErrorAction SilentlyContinue) {
        return $true
    }
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    return $false
}

function Invoke-LocalMode {
    Push-Location $RepoRoot
    try {
        New-Item -ItemType Directory -Force -Path $LocalRunDir | Out-Null
        $backendPidFile = Join-Path $LocalRunDir 'backend.pid'
        $frontendPidFile = Join-Path $LocalRunDir 'frontend.pid'

        if ($Stop) {
            Stop-TrackedProcess -Name 'backend' -PidFile $backendPidFile
            Stop-TrackedProcess -Name 'frontend' -PidFile $frontendPidFile
            Write-Step "Stopping db/redis containers"
            docker compose stop db redis
            return
        }

        Confirm-EnvFile

        if ($Reload) {
            Stop-TrackedProcess -Name 'backend' -PidFile $backendPidFile
            Stop-TrackedProcess -Name 'frontend' -PidFile $frontendPidFile
        }

        Write-Step "Ensuring db/redis are up (via Docker - this script doesn't install a local Postgres/Redis)"
        docker compose up -d db redis
        if ($LASTEXITCODE -ne 0) { throw "docker compose up db redis failed" }

        # .env's DATABASE_URL/REDIS_URL point at the Docker-internal "db"/
        # "redis" hostnames (correct for docker-compose, which overrides them
        # for the api container anyway) - a process started directly on the
        # host can't resolve those. Override for this process and everything
        # it spawns (alembic, python run.py) with the localhost + published-
        # port equivalents instead; .env itself is never touched.
        $localConn = Get-LocalConnectionEnv
        $env:DATABASE_URL = $localConn.DatabaseUrl
        $env:REDIS_URL = $localConn.RedisUrl

        # --- Backend + frontend installs run in the background, in parallel -
        # independent toolchains/directories, no reason to pay for one before
        # starting the other. Each is gated the same way: only when missing,
        # or -Reload was explicitly passed (matches the "picks up .env edits"
        # contract described above - a plain re-run trusts what's already
        # installed instead of re-resolving it every time). ---
        $venvPython = Join-Path $RepoRoot 'backend\.venv\Scripts\python.exe'
        $venvExisted = Test-Path $venvPython
        if (-not $venvExisted) {
            Write-Step "Creating backend virtualenv"
            python -m venv (Join-Path $RepoRoot 'backend\.venv')
        }
        $nodeModules = Join-Path $RepoRoot 'frontend\node_modules'

        $installJobs = @()
        if ((-not $venvExisted) -or $Reload) {
            Write-Step "Installing/updating backend dependencies (backgrounded, alongside the frontend install)"
            $installJobs += Start-Job -Name 'backend-install' -ScriptBlock {
                param($VenvPython, $RequirementsFile)
                & $VenvPython -m pip install -q -r $RequirementsFile
                $LASTEXITCODE
            } -ArgumentList $venvPython, (Join-Path $RepoRoot 'backend\requirements-dev.txt')
        }
        if ((-not (Test-Path $nodeModules)) -or $Reload) {
            Write-Step "Installing frontend dependencies (backgrounded, alongside the backend install)"
            $installJobs += Start-Job -Name 'frontend-install' -ScriptBlock {
                param($FrontendDir)
                Push-Location $FrontendDir
                npm install
                $LASTEXITCODE
            } -ArgumentList (Join-Path $RepoRoot 'frontend')
        }
        if ($installJobs.Count -gt 0) {
            $installJobs | Wait-Job | Out-Null
            foreach ($job in $installJobs) {
                $exitCode = Receive-Job -Job $job
                Remove-Job -Job $job
                if ($exitCode -ne 0) { throw "$($job.Name) failed (exit $exitCode) - re-run with the job's command directly to see full output" }
            }
        }

        Write-Step "Running Alembic migrations"
        Push-Location (Join-Path $RepoRoot 'backend')
        & $venvPython -m alembic upgrade head
        Pop-Location
        if ($LASTEXITCODE -ne 0) { throw "alembic upgrade head failed" }

        if (-not (Test-TrackedProcessAlive -PidFile $backendPidFile)) {
            Write-Step "Starting backend (python run.py) - logging to .local_run\backend.log"
            $backendProc = Start-Process -FilePath $venvPython -ArgumentList 'run.py' `
                -WorkingDirectory (Join-Path $RepoRoot 'backend') `
                -RedirectStandardOutput (Join-Path $LocalRunDir 'backend.log') `
                -RedirectStandardError (Join-Path $LocalRunDir 'backend.err.log') `
                -PassThru -WindowStyle Hidden
            Set-Content -Path $backendPidFile -Value $backendProc.Id -NoNewline
        } else {
            Write-Step "Backend already running (pid $(Get-Content $backendPidFile)) - pass -Reload to restart it"
        }

        if (-not (Test-TrackedProcessAlive -PidFile $frontendPidFile)) {
            Write-Step "Starting frontend (npm run dev) - logging to .local_run\frontend.log"
            $frontendProc = Start-Process -FilePath 'npm' -ArgumentList 'run', 'dev' `
                -WorkingDirectory (Join-Path $RepoRoot 'frontend') `
                -RedirectStandardOutput (Join-Path $LocalRunDir 'frontend.log') `
                -RedirectStandardError (Join-Path $LocalRunDir 'frontend.err.log') `
                -PassThru -WindowStyle Hidden
            Set-Content -Path $frontendPidFile -Value $frontendProc.Id -NoNewline
        } else {
            Write-Step "Frontend already running (pid $(Get-Content $frontendPidFile)) - pass -Reload to restart it"
        }

        if (Wait-ForHealth) {
            $creds = Get-BootstrapCredentials
            Write-Host ""
            Write-Host "Web:   http://localhost:3000 (npm run dev)" -ForegroundColor Green
            Write-Host "API:   http://localhost:8000/docs (python run.py)" -ForegroundColor Green
            Write-Host "Login: $($creds.Email) / $($creds.Password)" -ForegroundColor Green
            Write-Host "Logs:  .local_run\backend.log / .local_run\frontend.log" -ForegroundColor Green
        }
    } finally {
        Pop-Location
    }
}

if ($Mode -eq 'docker') {
    Invoke-DockerMode
} else {
    Invoke-LocalMode
}
