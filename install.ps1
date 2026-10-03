$ErrorActionPreference = 'Stop'
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonExe = (& py -3 -c 'import sys; print(sys.executable)')
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $PythonExe)) {
    throw 'Python 3 is required. Install Python and rerun install.ps1.'
}
& $PythonExe -m venv (Join-Path $ProjectDir '.venv-local')
$VenvPython = Join-Path $ProjectDir '.venv-local\Scripts\python.exe'
& $VenvPython -m pip install -r (Join-Path $ProjectDir 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
Write-Host 'Installation complete.'
