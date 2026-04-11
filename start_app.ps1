$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

$pythonExe = "C:\Users\soumy\AppData\Local\Programs\Python\Python310\python.exe"

if (-not (Test-Path $pythonExe)) {
    Write-Host "Python was not found at:`n$pythonExe" -ForegroundColor Red
    Write-Host "Update start_app.ps1 with the correct Python path and try again."
    exit 1
}

Write-Host "Starting Flask app from: $projectRoot"
& $pythonExe "$projectRoot\app.py"
