# Starts the MCP server, backend API and Angular frontend. Ctrl+C stops all three.
$root = $PSScriptRoot
$env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')
$env:NG_CLI_ANALYTICS = 'false'   # skip the interactive Angular analytics prompt

function Test-Port($port) { [bool](Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) }

$procs = @()
try {
  if (Test-Port 8765) { Write-Host 'MCP server already running on 8765' } else {
    $procs += Start-Process python -ArgumentList '-m','mcp_server.server' -WorkingDirectory "$root\backend" -NoNewWindow -PassThru
  }
  if (Test-Port 8000) { Write-Host 'Backend already running on 8000' } else {
    $procs += Start-Process python -ArgumentList '-m','uvicorn','app.main:app','--port','8000','--reload' -WorkingDirectory "$root\backend" -NoNewWindow -PassThru
  }
  if (Test-Port 4200) { Write-Error 'Port 4200 is already in use (the backend only allows CORS from 4200). Stop that process first.'; return }
  if (-not (Test-Path "$root\frontend-ng\node_modules")) { npm --prefix "$root\frontend-ng" install }
  Write-Host 'Open http://localhost:4200  (login: randy / password)'
  npm --prefix "$root\frontend-ng" start
} finally {
  foreach ($p in $procs) { if (-not $p.HasExited) { taskkill /PID $p.Id /T /F | Out-Null } }
}
