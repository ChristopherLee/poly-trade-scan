$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$Port = 8050
$DashboardUrl = "http://localhost:$Port/api/summary"
$PythonExe = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$DashboardScript = Join-Path $RepoRoot "dashboard.py"
$LogDir = Join-Path $RepoRoot "assets\logs"
$StdOutLog = Join-Path $LogDir "dashboard.stdout.log"
$StdErrLog = Join-Path $LogDir "dashboard.stderr.log"

if (-not (Test-Path $PythonExe)) {
    throw "Python interpreter not found at $PythonExe"
}

if (-not (Test-Path $DashboardScript)) {
    throw "dashboard.py not found at $DashboardScript"
}

New-Item -ItemType Directory -Path $LogDir -Force | Out-Null

function Get-DashboardListenerPids {
    $lines = netstat -ano -p tcp | Select-String ":$Port\s+.*LISTENING\s+(\d+)$"
    $pids = New-Object System.Collections.Generic.HashSet[int]
    foreach ($line in $lines) {
        if ($line.Matches.Count -gt 0) {
            [void]$pids.Add([int]$line.Matches[0].Groups[1].Value)
        }
    }
    return @($pids)
}

function Stop-DashboardListeners {
    $pids = Get-DashboardListenerPids
    if (-not $pids.Count) {
        Write-Host "No listeners on port $Port."
        return
    }

    foreach ($listenerPid in $pids) {
        try {
            $proc = Get-Process -Id $listenerPid -ErrorAction Stop
            Write-Host "Stopping PID $listenerPid ($($proc.ProcessName)) on port $Port..."
            Stop-Process -Id $listenerPid -Force -ErrorAction Stop
        } catch {
            Write-Warning ("Failed to stop PID {0}: {1}" -f $listenerPid, $_.Exception.Message)
        }
    }

    Start-Sleep -Seconds 1
}

function Wait-ForPortState {
    param(
        [bool]$ShouldBeListening,
        [int]$TimeoutSeconds = 20
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        $hasListeners = (Get-DashboardListenerPids).Count -gt 0
        if ($ShouldBeListening -eq $hasListeners) {
            return $true
        }
        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $deadline)

    return $false
}

function Wait-ForDashboard {
    param(
        [System.Diagnostics.Process]$Process,
        [int]$TimeoutSeconds = 20
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        if ($Process.HasExited) {
            return $false
        }
        $Process.Refresh()
        try {
            $response = Invoke-WebRequest -UseBasicParsing $DashboardUrl -TimeoutSec 3
            if ($response.StatusCode -eq 200) {
                return $true
            }
        } catch {
        }
        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $deadline)

    return $false
}

Stop-DashboardListeners
if (-not (Wait-ForPortState -ShouldBeListening $false -TimeoutSeconds 20)) {
    $remainingPids = (Get-DashboardListenerPids) -join ", "
    throw "Port $Port did not clear after stopping listeners. Remaining PIDs: $remainingPids"
}

Write-Host "Starting dashboard from $DashboardScript..."
$proc = Start-Process `
    -FilePath $PythonExe `
    -ArgumentList $DashboardScript `
    -WorkingDirectory $RepoRoot `
    -RedirectStandardOutput $StdOutLog `
    -RedirectStandardError $StdErrLog `
    -PassThru

if (-not (Wait-ForPortState -ShouldBeListening $true -TimeoutSeconds 20)) {
    $stderr = ""
    if (Test-Path $StdErrLog) {
        $stderr = (Get-Content $StdErrLog -Tail 20 | Out-String).Trim()
    }
    throw "Dashboard process did not bind to port $Port. PID=$($proc.Id). Stderr: $stderr"
}

if (-not (Wait-ForDashboard -Process $proc)) {
    $stderr = ""
    if (Test-Path $StdErrLog) {
        $stderr = (Get-Content $StdErrLog -Tail 20 | Out-String).Trim()
    }
    throw "Dashboard failed to become healthy on $DashboardUrl. PID=$($proc.Id). Stderr: $stderr"
}

Write-Host "Dashboard is healthy on http://localhost:$Port/ (PID $($proc.Id))."
Write-Host "stdout: $StdOutLog"
Write-Host "stderr: $StdErrLog"
