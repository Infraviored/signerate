# run_app.ps1
# This script safely kills any existing server instances and restarts the app.

$port = 5000

Write-Host "--- Cleaning up port $port ---" -ForegroundColor Cyan

# 1. Find and kill processes listening on the target port
$connections = netstat -ano | Select-String ":$port\s+.*\s+LISTENING"
foreach ($conn in $connections) {
    if ($conn -match "\s+(?<pid>\d+)$") {
        $pidToKill = $Matches['pid']
        Write-Host "Killing Process ID $pidToKill listening on port $port..." -ForegroundColor Yellow
        Stop-Process -Id $pidToKill -Force -ErrorAction SilentlyContinue
    }
}

# 2. Kill any other python processes running app.py in this directory
Get-CimInstance Win32_Process -Filter "name = 'python.exe' AND commandline LIKE '%app.py%'" | ForEach-Object {
    Write-Host "Killing orphaned python process: $($_.ProcessId)" -ForegroundColor Yellow
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
}

Write-Host "--- Starting Server ---" -ForegroundColor Green
$env:PYTHONUNBUFFERED=1
.\venv\Scripts\python.exe app.py
