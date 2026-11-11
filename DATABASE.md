# Database setup: the same data on another computer

Plan Assistant uses two databases. Both must hold the same data on the new computer.

| Database | Used for | Tables | Connection settings in `.env` |
|---|---|---|---|
| **MySQL 8** `plan_assistant` | Members, their e-payments and their activity | `user_account`, `users_profile`, `epayment_method`, `epayment`, `conversations`, `messages`, `conversation_memory`, `admins`, `agent_turns`, `agent_events` | `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_DATABASE` |
| **Qdrant** (`qdrant/qdrant`) | Policy, FAQ, PDF knowledge base, embeddings, settings | Collections `plan_settings`, `policy_topics`, `faq_entries`, `faq_learned`, `sag_aftra_knowledge` | `QDRANT_URL` (Docker server) **or** `QDRANT_PATH` (embedded, one process only) |

There are two ways to get "same database everywhere". Pick one.

- **Option A: one shared database server.** Every computer connects to the same host. No copying, and the data is always identical. Section 2.
- **Option B: copy the data to a local database on the new computer.** Each computer has its own copy. Section 3.

Commands are PowerShell. macOS/Linux differences are noted.

---

## 1. Install the database software on the new computer

**MySQL 8.0**
```powershell
winget install Oracle.MySQL          # or download MySQL Installer from https://dev.mysql.com/downloads/
mysql --version
```
macOS: `brew install mysql && brew services start mysql`. Ubuntu: `sudo apt install mysql-server`.

**Qdrant**: run the `qdrant/qdrant` Docker image with `docker compose up -d qdrant` from the project root (Docker Desktop with WSL 2 on Windows), then set `QDRANT_URL=http://127.0.0.1:6333`. Without Docker, `QDRANT_PATH=<folder>` (or `:memory:`) runs Qdrant inside the Python process, which works for one process only; the backend and MCP server are separate processes, so use it only with `MCP_SERVER_URL` empty or for tests.

---

## 2. Option A: share one database server

### 2.1 MySQL on a shared host

On the host computer, create a dedicated user (do not use `root` remotely):

```sql
CREATE DATABASE IF NOT EXISTS plan_assistant CHARACTER SET utf8mb4;
CREATE USER 'plan_app'@'%' IDENTIFIED BY 'a-strong-password';
GRANT ALL PRIVILEGES ON plan_assistant.* TO 'plan_app'@'%';
FLUSH PRIVILEGES;
```

Allow remote connections: set `bind-address = 0.0.0.0` in `my.ini` (Windows: `C:\ProgramData\MySQL\MySQL Server 8.0\my.ini`) or `/etc/mysql/mysql.conf.d/mysqld.cnf`, restart MySQL, and open TCP **3306** in the host firewall for the other computers only.

```powershell
# Windows host: allow 3306 from one office subnet
New-NetFirewallRule -DisplayName "MySQL 3306" -Direction Inbound -Protocol TCP -LocalPort 3306 -RemoteAddress 192.168.1.0/24 -Action Allow
```

On every other computer, put the same values in `.env`:

```ini
MYSQL_HOST=192.168.1.20        # the host computer's IP or DNS name
MYSQL_PORT=3306
MYSQL_USER=plan_app
MYSQL_PASSWORD=a-strong-password
MYSQL_DATABASE=plan_assistant
```

Test from the new computer: `mysql -h 192.168.1.20 -u plan_app -p plan_assistant -e "SELECT COUNT(*) FROM user_account;"`

### 2.2 Qdrant on a shared host

Run the Docker image on the host, with a persistent volume and an API key:

```powershell
docker run -d --name plan-qdrant -p 6333:6333 -e QDRANT__SERVICE__API_KEY=a-strong-key -v plan_qdrant:/qdrant/storage qdrant/qdrant:v1.20.0
```

Then in every computer's `.env` set:

```ini
QDRANT_URL=http://192.168.1.20:6333
QDRANT_API_KEY=a-strong-key
```

Load the collections and content once (from any one computer):

```powershell
cd backend
python -m mcp_server.init_qdrant
```

Use TLS (an `https://` URL behind a reverse proxy) for anything beyond a trusted network. Qdrant Cloud works the same way with its cluster URL and API key.

---

## 3. Option B: copy the data to the new computer

Use this when the new computer runs its own databases. Do the **export** on the old computer and the **import** on the new one. Copy the two files between them (USB, network share, `scp`).

### 3.1 Export on the old computer

```powershell
cd E:\equester-project\PlanAssistant
New-Item -ItemType Directory -Force dbbackup | Out-Null

# --- MySQL: schema + data in one file ---
$env:MYSQL_PWD = "your-mysql-password"        # avoids the password prompt
& "C:\Program Files\MySQL\MySQL Server 8.0\bin\mysqldump.exe" -u root --single-transaction --routines --triggers `
  --default-character-set=utf8mb4 --databases plan_assistant --result-file=dbbackup\mysql_plan_assistant.sql
Remove-Item Env:MYSQL_PWD

# --- Qdrant: only needed to keep FAQ hit counts; otherwise re-seed in 3.2 ---
foreach ($c in 'plan_settings','faq_entries','faq_learned') {
  $name = (Invoke-RestMethod -Method Post "http://localhost:6333/collections/$c/snapshots").result.name
  Invoke-WebRequest "http://localhost:6333/collections/$c/snapshots/$name" -OutFile "dbbackup\qdrant_$c.snapshot"
}
Get-ChildItem dbbackup
```

With an API key, add `-Headers @{ 'api-key' = '<key>' }` to each request. Policies, the 300-question PDF knowledge base and FAQ text are rebuilt from the files in `backend/app/data`, so they need no export.

macOS/Linux MySQL: `mysqldump -u root -p --single-transaction --routines --triggers --databases plan_assistant > dbbackup/mysql_plan_assistant.sql`

> The `dbbackup` files contain real member data and password hashes. Do **not** commit them. Add `dbbackup/` to `.gitignore` and delete the files after the import.

### 3.2 Import on the new computer

First finish steps 1 to 4 of [INSTALL.md](INSTALL.md) (clone, `.env`, `pip install`) but **skip** `init_db`, because the import creates everything.

```powershell
cd PlanAssistant

# --- MySQL (the dump creates the database and tables) ---
cmd /c "mysql -u root -p < dbbackup\mysql_plan_assistant.sql"
# macOS/Linux: mysql -u root -p < dbbackup/mysql_plan_assistant.sql

# --- Qdrant ---
```

Start Qdrant (`docker compose up -d qdrant`), then either re-seed everything from the files, which loses only FAQ hit counts:

```powershell
cd backend
python -m mcp_server.init_qdrant
```

or restore the snapshots from 3.1 first (keeps hit counts), then run `python -m mcp_server.init_qdrant` to load the rest:

```powershell
foreach ($c in 'plan_settings','faq_entries','faq_learned') {
  curl.exe -X POST "http://localhost:6333/collections/$c/snapshots/upload?priority=snapshot" -F "snapshot=@dbbackup/qdrant_$c.snapshot"
}
```

### 3.3 Make the `.env` match

Same values as the old computer, except the host and password if they differ:

```ini
MYSQL_HOST=localhost
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=<this computer's MySQL password>
MYSQL_DATABASE=plan_assistant
QDRANT_URL=http://localhost:6333   # plus QDRANT_API_KEY if the server has one
JWT_SECRET=<copy the old one>  # logins stay valid only if this matches
MCP_INTERNAL_SECRET=<copy the old one>
```

---

## 4. Verify both databases match

Run on **each** computer and compare the numbers.

```powershell
# MySQL
mysql -u root -p plan_assistant -e "SELECT 'user_account' t, COUNT(*) n FROM user_account UNION ALL SELECT 'epayment', COUNT(*) FROM epayment UNION ALL SELECT 'epayment_method', COUNT(*) FROM epayment_method UNION ALL SELECT 'conversations', COUNT(*) FROM conversations UNION ALL SELECT 'messages', COUNT(*) FROM messages; SELECT epmt_status, result, COUNT(*) FROM epayment GROUP BY epmt_status, result;"

# Qdrant
$h = @{}   # or @{ 'api-key' = '<key>' }
foreach ($c in 'plan_settings','policy_topics','faq_entries','faq_learned','sag_aftra_knowledge') { "$c = " + (Invoke-RestMethod -Method Post "http://localhost:6333/collections/$c/points/count" -Headers $h -ContentType 'application/json' -Body '{"exact":true}').result.count }
```

Then check the app: `cd backend; python -m pytest -q`, start with `.\start.ps1`, sign in as `randy` / `password`, and ask "What is the grace period?" and "How much do I owe?".

---

## 5. Keeping them in sync later

| Need | Do this |
|---|---|
| Everyone sees live changes | Use Option A (one shared server) |
| Refresh a copy from the old computer | Repeat sections 3.1 and 3.2. `mysqldump --databases` includes `DROP TABLE IF EXISTS`, so the import replaces the tables |
| Only policy / FAQ text or the PDF changed | Edit `backend/app/data/*.json` or replace the PDF, then `python -m mcp_server.init_qdrant` (changed records are re-embedded, removed ones deleted; FAQ hit counts are kept). `--reset` rebuilds everything and clears hit counts |
| Only the plan thresholds changed | `python -m mcp_server.init_qdrant --overwrite-settings` |
| Add the demo members to an empty database | `python -m mcp_server.init_db` (inserts missing rows only) |

**Never** run `python -m mcp_server.init_db --reset` on a database that holds real data: it drops `user_account`, `epayment` and every other table it owns.

## 6. Troubleshooting

| Error | Fix |
|---|---|
| `Can't connect to MySQL server on 'HOST'` | MySQL not listening on the network (`bind-address`), firewall closed on 3306, or wrong IP |
| `Access denied for user` | Wrong password, or the user was created for `'localhost'` only; create it for `'%'` or the client IP |
| `Unknown database 'plan_assistant'` | Run the import in 3.2, or `CREATE DATABASE plan_assistant` |
| `Connection refused` on port 6333 | Qdrant is not running. Start Docker Desktop, then `docker compose up -d qdrant` |
| `Storage folder ... is already accessed by another instance` | `QDRANT_PATH` embedded mode allows one process only; use `QDRANT_URL` with the Docker server |
| App says "Plan settings are unavailable" | MCP server cannot reach Qdrant. Check `QDRANT_URL` and that the container is healthy, then restart `python -m mcp_server.server` |
| Users cannot log in after the move | `JWT_SECRET` differs from the old computer, or the `user_account` table was not imported |
| A member shows no name in the app | `users_profile` was not imported. `USER_ACCOUNT` holds no name columns, so the display name comes from `users_profile` |
