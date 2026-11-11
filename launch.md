# Launch Plan Assistant

Run all commands from the project root `E:\equester-project\PlanAssistant` unless noted.

## One command

Starts the MCP server, backend and frontend. Ctrl+C stops all.

```powershell
.\start.ps1
```

## Manually (separate terminals)

```powershell
# Terminal 1 - MCP server (only needed if MCP_SERVER_URL is set)
cd backend
python -m mcp_server.server
```

```powershell
# Terminal 2 - Backend (http://localhost:8000, docs at /docs)
cd backend
python -m uvicorn app.main:app --port 8000 --reload
```

```powershell
# Terminal 3 - Frontend (http://localhost:4200)
cd frontend-ng
npm start
```

## First-time setup

```powershell
cd backend; pip install -r requirements.txt
cd ..\frontend-ng; npm install
```

## Optional services (Valkey / Qdrant)

```powershell
docker compose up -d
cd backend; python -m mcp_server.init_db; python -m mcp_server.init_qdrant
```

## start.ps1

[start.ps1](./start.ps1) starts the MCP server (8765), backend (8000) and frontend (4200), skipping any that are already running.

```powershell
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
```

## compose.yaml

[compose.yaml](./compose.yaml) defines the Valkey (cache, 6379) and Qdrant (vectors, 6333) containers. Start with `docker compose up -d`, stop with `docker compose down`. Set `VALKEY_URL=redis://127.0.0.1:6379/0` and `QDRANT_URL=http://127.0.0.1:6333` in `.env`.

```yaml
services:
  valkey:
    image: valkey/valkey:8.1-alpine
    restart: unless-stopped
    ports:
      - "127.0.0.1:6379:6379"
    command: ["valkey-server", "--maxmemory", "256mb", "--maxmemory-policy", "allkeys-lfu", "--save", "", "--appendonly", "no"]
    healthcheck:
      test: ["CMD", "valkey-cli", "ping"]
      interval: 10s
      timeout: 3s
      retries: 5

  qdrant:
    image: qdrant/qdrant:v1.20.0
    restart: unless-stopped
    ports:
      - "127.0.0.1:6333:6333"
      - "127.0.0.1:6334:6334"
    volumes:
      - qdrant_storage:/qdrant/storage
    # Add QDRANT__SERVICE__API_KEY under `environment:` (and QDRANT_API_KEY in .env) before exposing this beyond localhost.
    healthcheck:
      test: ["CMD-SHELL", "bash -c ':> /dev/tcp/127.0.0.1/6333' || exit 1"]
      interval: 10s
      timeout: 3s
      retries: 5

volumes:
  qdrant_storage:
```

## Sign in

Open http://localhost:4200 and sign in with `randy` / `password`.

- Use port 4200: the backend only allows CORS from that origin.
- The admin console is at http://localhost:4200/admin and needs your username in `ADMIN_USERS` in `.env`.
