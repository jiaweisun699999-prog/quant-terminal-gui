param(
  # 留空则依次尝试：本目录 .venv、上一级 .venv、PATH 中的 python
  [string]$PythonExe = ""
)

$ErrorActionPreference = "Stop"
$here = $PSScriptRoot

if ([string]::IsNullOrWhiteSpace($PythonExe)) {
  $c1 = Join-Path $here ".venv\Scripts\python.exe"
  $c2 = Join-Path $here "..\.venv\Scripts\python.exe"
  if (Test-Path $c1) {
    $PythonExe = (Resolve-Path $c1).Path
  }
  elseif (Test-Path $c2) {
    $PythonExe = (Resolve-Path $c2).Path
  }
  else {
    $PythonExe = "python"
  }
}

Write-Host "Using Python: $PythonExe" -ForegroundColor DarkGray

Write-Host "Installing requirements..." -ForegroundColor Cyan
Push-Location $here
try {
  & $PythonExe -m pip install -r "requirements.txt"

  Write-Host "Building Quant-Terminal.exe..." -ForegroundColor Cyan

# --noconsole: do not open a console window
# --onefile: single executable
# --collect-data akshare: AkShare 包内 JSON（如 file_fold/calendar.json）
# --collect-data py_mini_racer: mini_racer.dll + icudtl.dat（stock_index_pe_lg 等接口依赖）
& $PythonExe -m PyInstaller `
  --noconsole `
  --onefile `
  --name "Quant-Terminal" `
  --hidden-import "akshare" `
  --hidden-import "certifi" `
  --hidden-import "py_mini_racer" `
  --hidden-import "qt_worker" `
  --collect-data "akshare" `
  --collect-data "py_mini_racer" `
  "qt_app.py"

  Write-Host "Done. Output in .\dist\Quant-Terminal.exe" -ForegroundColor Green
}
finally {
  Pop-Location
}

