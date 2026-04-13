$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

$preferredPython = "C:\Users\soumy\AppData\Local\Programs\Python\Python310\python.exe"
$pythonCommand = $null
$pythonArgs = @()

if (Test-Path $preferredPython) {
    $pythonCommand = $preferredPython
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    $pythonCommand = "py"
    $pythonArgs = @("-3.10")
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $pythonCommand = "python"
} else {
    Write-Host "Python was not found. Install Python 3.10+ or update start_app.ps1." -ForegroundColor Red
    exit 1
}

Write-Host "Starting Flask app from: $projectRoot"
& $pythonCommand @pythonArgs "$projectRoot\app.py"
